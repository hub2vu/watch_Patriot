from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from . import __version__
from .autostart import install_autostart, uninstall_autostart
from .config import AppConfig, ensure_user_files, load_config
from .db import Database
from .dcinside import download_image_bytes, fetch_post_image_urls, fetch_recent_posts
from .image_scan import compute_phash, compute_sha256, compute_tile_phashes, scan_post_images
from .local_popup import enqueue_popup, show_test_popup
from .logging_setup import setup_logging
from .models import Alert, BadHash, ImageHashRecord, Post, Risk

log = logging.getLogger(__name__)

PopupHandler = Callable[[Alert], None]
StatusHandler = Callable[[str], None]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "gui":
        from .gui import main as gui_main

        gui_main()
        return 0
    if args.command == "test-popup":
        show_test_popup()
        return 0

    config = load_config(getattr(args, "config", None))
    ensure_user_files(config)
    setup_logging(config)
    db = Database(config.database_file)
    db.migrate()

    if args.command == "watch":
        return run_watch(config, db=db, once=False)
    if args.command == "once":
        return run_watch(config, db=db, once=True)
    if args.command == "remember-post":
        count = db.remember_post_hashes(args.post_no, args.label)
        print(f"registered {count} image hash record(s) from post {args.post_no}")
        return 0 if count else 1
    if args.command == "remember-file":
        bad_id = remember_file(db, Path(args.image_path), args.label, config)
        print(f"registered bad hash id {bad_id} from file {args.image_path}")
        return 0
    if args.command == "stats":
        for key, value in db.stats().items():
            print(f"{key}: {value}")
        return 0
    if args.command == "install-autostart":
        result = install_autostart()
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    if args.command == "uninstall-autostart":
        result = uninstall_autostart()
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dc_watch", description="DCWatch local popup monitor")
    parser.add_argument("--version", action="version", version=f"dc-watch {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("watch", "once"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", help="Path to config.toml")

    subparsers.add_parser("gui")
    subparsers.add_parser("test-popup")

    remember_post = subparsers.add_parser("remember-post")
    remember_post.add_argument("post_no")
    remember_post.add_argument("label")
    remember_post.add_argument("--config", help="Path to config.toml")

    remember_file = subparsers.add_parser("remember-file")
    remember_file.add_argument("image_path")
    remember_file.add_argument("label")
    remember_file.add_argument("--config", help="Path to config.toml")

    stats = subparsers.add_parser("stats")
    stats.add_argument("--config", help="Path to config.toml")

    install = subparsers.add_parser("install-autostart")
    install.add_argument("--config", help=argparse.SUPPRESS)

    uninstall = subparsers.add_parser("uninstall-autostart")
    uninstall.add_argument("--config", help=argparse.SUPPRESS)
    return parser


def run_watch(
    config: AppConfig,
    db: Database | None = None,
    once: bool = False,
    stop_event: threading.Event | None = None,
    popup_handler: PopupHandler | None = None,
    status_handler: StatusHandler | None = None,
) -> int:
    database = db or Database(config.database_file)
    database.migrate()
    log.info("DCWatch started for gallery %s", config.gallery_id)
    status_handler = status_handler or (lambda message: None)
    try:
        if once:
            status_handler("checking")
            try:
                check_once(config, database, popup_handler=popup_handler, status_handler=status_handler)
            except Exception as exc:
                log.exception("watch iteration failed")
                status_handler(f"error: {exc.__class__.__name__}")
            return 0

        baseline_current_posts(config, database, status_handler)
        queue: list[Post] = []
        queued_post_nos: set[str] = set()
        latest_checked_title = "없음"
        next_poll_at = time.monotonic()
        next_process_at = time.monotonic()
        while stop_event is None or not stop_event.is_set():
            now = time.monotonic()
            did_work = False

            if now >= next_poll_at:
                try:
                    added = enqueue_new_image_posts(config, database, queue, queued_post_nos)
                    status_handler(_format_queue_status(True, latest_checked_title, len(queue), added_count=len(added)))
                except Exception as exc:
                    log.exception("gallery poll failed")
                    status_handler(f"error: {exc.__class__.__name__}")
                next_poll_at = now + max(1, config.poll_seconds)
                did_work = True

            if queue and now >= next_process_at:
                post = queue.pop(0)
                queued_post_nos.discard(post.post_no)
                latest_checked_title = _shorten(post.title or "(제목 없음)", 80)
                try:
                    alert = process_image_post(config, database, post, popup_handler)
                    status_handler(_format_watch_status(True, latest_checked_title, 1 if alert else 0, queue_count=len(queue)))
                except Exception as exc:
                    log.exception("queued image post scan failed for %s", post.post_no)
                    status_handler(f"error: {exc.__class__.__name__} | queue={len(queue)}")
                next_process_at = now + max(0, config.jitter_seconds)
                did_work = True

            if did_work:
                continue

            wait_until = next_poll_at
            if queue:
                wait_until = min(wait_until, next_process_at)
            delay = max(0.0, wait_until - time.monotonic())
            if delay <= 0:
                continue
            _sleep_with_stop(delay, stop_event)
    except KeyboardInterrupt:
        log.info("DCWatch stopped by keyboard interrupt")
    finally:
        status_handler("감시 중지됨")
    return 0


def baseline_current_posts(config: AppConfig, db: Database, status_handler: StatusHandler | None = None) -> int:
    status_handler = status_handler or (lambda message: None)
    posts = _image_posts(fetch_recent_posts(config))
    for post in posts:
        db.upsert_seen_post(post, risk="baseline")
    latest_title = _latest_post_title(posts)
    status_handler(f"감시 기준선 저장: 현재 이미지글 {len(posts)}개 | 최근 이미지글: {latest_title}")
    log.info("watch-start baseline saved with %s visible image post(s)", len(posts))
    return len(posts)


def check_once(
    config: AppConfig,
    db: Database,
    popup_handler: PopupHandler | None = None,
    status_handler: StatusHandler | None = None,
) -> list[Alert]:
    status_handler = status_handler or (lambda message: None)
    posts = _image_posts(fetch_recent_posts(config))
    latest_title = _latest_post_title(posts)
    first_run = db.seen_count() == 0
    if first_run and not config.scan_existing_on_first_run:
        for post in posts:
            db.upsert_seen_post(post, risk="none")
        log.info("first run baseline saved with %s post(s)", len(posts))
        status_handler(_format_watch_status(True, latest_title, 0))
        return []

    unseen = [post for post in posts if not db.has_seen_post(post.post_no)]
    alerts: list[Alert] = []
    for post in reversed(unseen):
        alert = process_image_post(config, db, post, popup_handler)
        if alert is not None:
            alerts.append(alert)
    status_handler(_format_watch_status(True, latest_title, len(alerts), queue_count=0))
    return alerts


def enqueue_new_image_posts(config: AppConfig, db: Database, queue: list[Post], queued_post_nos: set[str]) -> list[Post]:
    posts = _image_posts(fetch_recent_posts(config))
    candidates = [
        post
        for post in posts
        if post.post_no not in queued_post_nos and not db.has_seen_post(post.post_no) and not db.has_alert(post.post_no)
    ]
    added: list[Post] = []
    for post in reversed(candidates):
        queue.append(post)
        queued_post_nos.add(post.post_no)
        added.append(post)
    return added


def process_image_post(config: AppConfig, db: Database, post: Post, popup_handler: PopupHandler | None = None) -> Alert | None:
    alert = scan_post(config, db, post)
    db.upsert_seen_post(post, risk=alert.risk if alert else "none")
    if alert is None or db.has_alert(post.post_no):
        return None
    db.add_alert(alert)
    if _popup_enabled(config, alert.risk):
        _show_alert(config, db, alert, popup_handler)
    return alert


def scan_post(config: AppConfig, db: Database, post: Post) -> Alert | None:
    reasons: list[str] = []
    image_data: list[bytes] = []
    image_count = 0
    try:
        image_urls = fetch_post_image_urls(post.url)
    except Exception as exc:
        log.warning("image extraction failed for post %s: %s", post.post_no, exc.__class__.__name__)
        if config.alert_on_analysis_failure:
            return Alert(
                post_no=post.post_no,
                title=post.title,
                url=post.url,
                writer=post.writer,
                risk="warning",
                image_count=0,
                reasons=[f"image extraction failed: {exc.__class__.__name__}"],
            )
        return None

    for image_url in image_urls:
        try:
            image_data.append(download_image_bytes(image_url, referer=post.url, max_bytes=config.max_image_bytes))
            image_count += 1
        except Exception as exc:
            log.warning("image download failed for post %s: %s", post.post_no, exc.__class__.__name__)
            if config.alert_on_analysis_failure:
                reasons.append(f"image download failed: {exc.__class__.__name__}")

    bad_hashes = db.get_bad_hashes()
    result = scan_post_images(image_data, bad_hashes, config)
    db.add_post_image_hashes(post.post_no, result.image_hashes)
    risk = _max_risk(result.risk, "warning" if reasons and config.alert_on_analysis_failure else "none")
    all_reasons = _dedupe(result.reasons + reasons)
    if risk in ("high", "warning"):
        return Alert(
            post_no=post.post_no,
            title=post.title,
            url=post.url,
            writer=post.writer,
            risk=risk,
            image_count=max(image_count, result.image_count),
            reasons=all_reasons,
        )
    return None


def remember_file(db: Database, image_path: Path, label: str, config: AppConfig | None = None) -> int:
    config = config or AppConfig()
    data = image_path.read_bytes()
    sha = compute_sha256(data)
    try:
        phash = compute_phash(data)
    except Exception:
        phash = None
    tile_phashes: tuple[str, ...] = ()
    if phash and config.enable_tile_phash:
        try:
            tile_phashes = tuple(compute_tile_phashes(data, config.tile_phash_grid_size))
        except Exception:
            tile_phashes = ()
    return db.add_bad_hash(BadHash(label=label, sha256=sha, phash=phash, source_file=str(image_path), tile_phashes=tile_phashes))


def _show_alert(config: AppConfig, db: Database, alert: Alert, popup_handler: PopupHandler | None) -> None:
    if popup_handler:
        popup_handler(alert)
        return

    def remember(selected: Alert) -> None:
        label = f"confirmed_{selected.post_no}_{int(time.time())}"
        count = db.remember_post_hashes(selected.post_no, label)
        log.info("registered %s hash record(s) from post %s", count, selected.post_no)

    enqueue_popup(alert, topmost=(config.topmost_popup or alert.risk == "high"), on_remember_bad_hash=remember)


def _popup_enabled(config: AppConfig, risk: Risk) -> bool:
    return (risk == "high" and config.high_popup_enabled) or (risk == "warning" and config.warning_popup_enabled)


def _sleep_with_stop(delay: float, stop_event: threading.Event | None) -> None:
    if stop_event is None:
        time.sleep(delay)
        return
    stop_event.wait(delay)


def _max_risk(left: Risk, right: Risk) -> Risk:
    order = {"none": 0, "warning": 1, "high": 2}
    return left if order[left] >= order[right] else right


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _latest_post_title(posts: list[Post]) -> str:
    if not posts:
        return "없음"
    return _shorten(posts[0].title or "(제목 없음)", 80)


def _image_posts(posts: list[Post]) -> list[Post]:
    return [post for post in posts if post.has_image]


def _format_queue_status(running: bool, latest_title: str, queue_count: int, added_count: int = 0) -> str:
    state = "감시 실행 중" if running else "감시 중지됨"
    return f"{state} | 최근 체크 이미지글: {latest_title} | queue={queue_count} | added={added_count}"


def _format_watch_status(running: bool, latest_title: str, alert_count: int, queue_count: int = 0) -> str:
    state = "감시 실행 중" if running else "감시 중지됨"
    return f"{state} | 최근 체크 이미지글: {latest_title} | queue={queue_count} | alerts={alert_count}"


def _shorten(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
