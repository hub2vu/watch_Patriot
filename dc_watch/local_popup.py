from __future__ import annotations

import logging
import re
import webbrowser
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Callable

from .models import Alert

log = logging.getLogger(__name__)

OpenCallback = Callable[[Alert], None]
RememberCallback = Callable[[Alert], None]

_URL_RE = re.compile(r"https?://[^\s)>\"]+")
_POPUP_LOCK = Lock()
_POPUP_QUEUE: deque["_QueuedPopup"] = deque()
_SHOWING = False


@dataclass(frozen=True)
class _QueuedPopup:
    alert: Alert
    topmost: bool
    on_open_post: OpenCallback | None
    on_remember_bad_hash: RememberCallback | None


def build_popup_message(alert: Alert) -> str:
    title = _strip_unapproved_urls(alert.title, alert.url)
    writer = _strip_unapproved_urls(alert.writer, alert.url) or "파싱 불가"
    safe_reasons = [_strip_unapproved_urls(reason, alert.url) for reason in alert.reasons]
    safe_reasons = [reason for reason in safe_reasons if reason.strip()]
    if len(safe_reasons) != len(alert.reasons) or title != alert.title:
        log.warning("Removed non-post URLs from popup text for post %s", alert.post_no)
    reason_text = "\n".join(f"- {reason}" for reason in safe_reasons) if safe_reasons else "- 위험 사유 없음"
    return "\n".join(
        [
            "[특갤 이미지 테러 의심]",
            f"위험도: {alert.risk}",
            f"글번호: {alert.post_no}",
            f"제목: {title}",
            f"작성자: {writer}",
            f"본문 이미지 수: {alert.image_count}",
            "위험 사유:",
            reason_text,
            f"게시글 URL: {alert.url}",
            "",
            "주의: 이 글을 열람하면 문제 이미지가 표시될 수 있습니다.",
            "이 팝업에는 이미지·썸네일·이미지 URL을 표시하지 않습니다.",
            "",
            "버튼: 게시글 열기 / 닫기 / 이 글을 확정 테러 해시 DB에 등록",
        ]
    )


def show_topmost_popup(
    alert: Alert,
    on_open_post: OpenCallback | None = None,
    on_remember_bad_hash: RememberCallback | None = None,
) -> None:
    show_popup(alert, topmost=True, on_open_post=on_open_post, on_remember_bad_hash=on_remember_bad_hash)


def show_popup(
    alert: Alert,
    topmost: bool,
    on_open_post: OpenCallback | None = None,
    on_remember_bad_hash: RememberCallback | None = None,
) -> None:
    _render_popup_window(alert, topmost, on_open_post=on_open_post, on_remember_bad_hash=on_remember_bad_hash)


def show_local_warning(alert: Alert) -> None:
    show_topmost_popup(alert)


def show_test_popup() -> None:
    show_topmost_popup(
        Alert(
            post_no="TEST",
            title="테스트 팝업",
            url="https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity",
            writer="DCWatch",
            risk="warning",
            image_count=0,
            reasons=["로컬 topmost 팝업 표시 테스트입니다."],
        )
    )


def enqueue_popup(
    alert: Alert,
    topmost: bool = True,
    on_open_post: OpenCallback | None = None,
    on_remember_bad_hash: RememberCallback | None = None,
) -> None:
    with _POPUP_LOCK:
        _POPUP_QUEUE.append(_QueuedPopup(alert, topmost, on_open_post, on_remember_bad_hash))
    _drain_queue()


def _drain_queue() -> None:
    global _SHOWING
    with _POPUP_LOCK:
        if _SHOWING or not _POPUP_QUEUE:
            return
        _SHOWING = True
        queued = _POPUP_QUEUE.popleft()
    try:
        show_popup(
            queued.alert,
            topmost=queued.topmost,
            on_open_post=queued.on_open_post,
            on_remember_bad_hash=queued.on_remember_bad_hash,
        )
    finally:
        with _POPUP_LOCK:
            _SHOWING = False
        if _POPUP_QUEUE:
            _drain_queue()


def _render_popup_window(
    alert: Alert,
    topmost: bool,
    on_open_post: OpenCallback | None = None,
    on_remember_bad_hash: RememberCallback | None = None,
) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("[특갤 이미지 테러 의심]")
    root.geometry("620x430")
    root.resizable(True, True)
    if topmost:
        root.attributes("-topmost", True)
    root.lift()
    try:
        root.focus_force()
    except tk.TclError:
        pass

    text = tk.Text(root, wrap="word", height=18, width=76)
    text.insert("1.0", build_popup_message(alert))
    text.configure(state="disabled")
    text.pack(fill="both", expand=True, padx=12, pady=(12, 8))

    buttons = tk.Frame(root)
    buttons.pack(fill="x", padx=12, pady=(0, 12))

    def open_post() -> None:
        if on_open_post:
            on_open_post(alert)
        else:
            webbrowser.open(alert.url)

    def remember() -> None:
        ok = messagebox.askyesno(
            "확정 테러 해시 DB 등록",
            "사용자가 직접 확인한 글의 저장된 이미지 해시를 확정 테러 해시 DB에 등록할까요?",
            parent=root,
        )
        if ok and on_remember_bad_hash:
            on_remember_bad_hash(alert)

    tk.Button(buttons, text="게시글 열기", command=open_post).pack(side="left")
    tk.Button(buttons, text="닫기", command=root.destroy).pack(side="right")
    tk.Button(buttons, text="이 글을 확정 테러 해시 DB에 등록", command=remember).pack(side="right", padx=(0, 8))
    root.mainloop()


def _strip_unapproved_urls(text: str, allowed_url: str) -> str:
    def replace(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".,")
        if url == allowed_url:
            return url
        return "[URL 제거됨]"

    return _URL_RE.sub(replace, text)
