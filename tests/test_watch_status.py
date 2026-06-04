from pathlib import Path
import threading

from dc_watch.cli import check_once, run_watch
from dc_watch.config import AppConfig
from dc_watch.db import Database
from dc_watch.models import Post


def test_check_once_reports_running_and_latest_checked_title(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    config = AppConfig()
    config._base_dir = tmp_path
    statuses: list[str] = []

    monkeypatch.setattr(
        "dc_watch.cli.fetch_recent_posts",
        lambda config: [Post(post_no="1", title="가장 최근 글", url="https://example.test/post/1", writer="w", has_image=True)],
    )
    monkeypatch.setattr("dc_watch.cli.fetch_post_image_urls", lambda url: [])

    alerts = check_once(config, db, status_handler=statuses.append)

    assert alerts == []
    assert statuses[-1] == "감시 실행 중 | 최근 체크 이미지글: 가장 최근 글 | queue=0 | alerts=0"


def test_run_watch_reports_stopped_when_loop_finishes(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    config = AppConfig()
    config._base_dir = tmp_path
    statuses: list[str] = []

    monkeypatch.setattr("dc_watch.cli.check_once", lambda *args, **kwargs: [])

    run_watch(config, db=db, once=True, status_handler=statuses.append)

    assert statuses[-1] == "감시 중지됨"


def test_check_once_ignores_text_only_posts_without_storing_seen(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    config = AppConfig()
    config._base_dir = tmp_path

    monkeypatch.setattr(
        "dc_watch.cli.fetch_recent_posts",
        lambda config: [Post(post_no="10", title="text only", url="https://example.test/post/10", writer="w", has_image=False)],
    )
    monkeypatch.setattr("dc_watch.cli.scan_post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("text post was scanned")))

    alerts = check_once(config, db)

    assert alerts == []
    assert db.has_seen_post("10") is False


def test_run_watch_baselines_current_posts_then_scans_new_image_posts(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    config = AppConfig()
    config._base_dir = tmp_path
    stop_event = threading.Event()
    fetch_calls = 0
    scanned: list[str] = []

    def fake_fetch_recent_posts(_config):
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return [Post(post_no="100", title="before start", url="https://example.test/post/100", writer="w", has_image=True)]
        return [
            Post(post_no="101", title="after start image", url="https://example.test/post/101", writer="w", has_image=True),
            Post(post_no="100", title="before start", url="https://example.test/post/100", writer="w", has_image=True),
        ]

    def fake_scan_post(_config, _db, post):
        scanned.append(post.post_no)
        return None

    monkeypatch.setattr("dc_watch.cli.fetch_recent_posts", fake_fetch_recent_posts)
    monkeypatch.setattr("dc_watch.cli.scan_post", fake_scan_post)
    monkeypatch.setattr("dc_watch.cli._sleep_with_stop", lambda delay, event: event.set())

    run_watch(config, db=db, once=False, stop_event=stop_event)

    assert scanned == ["101"]
    assert db.has_seen_post("100") is True
    assert db.has_seen_post("101") is True


def test_run_watch_baselines_only_image_posts_and_processes_queue_with_jitter(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    config = AppConfig(poll_seconds=45, jitter_seconds=10)
    config._base_dir = tmp_path
    stop_event = threading.Event()
    fetch_calls = 0
    now = [0.0]
    scanned: list[str] = []
    sleeps: list[float] = []
    statuses: list[str] = []

    def fake_fetch_recent_posts(_config):
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return [
                Post(post_no="100", title="baseline image", url="https://example.test/post/100", writer="w", has_image=True),
                Post(post_no="99", title="baseline text", url="https://example.test/post/99", writer="w", has_image=False),
            ]
        return [
            Post(post_no="102", title="newer image", url="https://example.test/post/102", writer="w", has_image=True),
            Post(post_no="101", title="older image", url="https://example.test/post/101", writer="w", has_image=True),
            Post(post_no="98", title="new text", url="https://example.test/post/98", writer="w", has_image=False),
        ]

    def fake_scan_post(_config, _db, post):
        scanned.append(post.post_no)
        return None

    def fake_sleep(delay: float, event: threading.Event | None) -> None:
        sleeps.append(delay)
        now[0] += delay
        if len(scanned) >= 2 and event is not None:
            event.set()

    monkeypatch.setattr("dc_watch.cli.fetch_recent_posts", fake_fetch_recent_posts)
    monkeypatch.setattr("dc_watch.cli.scan_post", fake_scan_post)
    monkeypatch.setattr("dc_watch.cli.time.monotonic", lambda: now[0])
    monkeypatch.setattr("dc_watch.cli._sleep_with_stop", fake_sleep)

    run_watch(config, db=db, once=False, stop_event=stop_event, status_handler=statuses.append)

    assert scanned == ["101", "102"]
    assert sleeps[0] == 10
    assert db.has_seen_post("100") is True
    assert db.has_seen_post("99") is False
    assert db.has_seen_post("98") is False
    assert any("queue=2" in status for status in statuses)
