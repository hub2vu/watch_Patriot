from pathlib import Path

from dc_watch.local_popup import build_popup_message, show_topmost_popup
from dc_watch.models import Alert


def _alert() -> Alert:
    return Alert(
        post_no="1234567",
        title="테스트 제목 http://images.example.test/a.jpg",
        url="https://gall.dcinside.com/mgallery/board/view/?id=thesingularity&no=1234567",
        writer="작성자",
        risk="high",
        image_count=2,
        reasons=[
            "bad pHash match",
            "thumbnail https://cdn.example.test/thumb.png should not leak",
            "image url https://cdn.example.test/original.jpeg should not leak",
        ],
    )


def test_popup_message_never_contains_image_or_thumbnail_urls() -> None:
    message = build_popup_message(_alert())

    assert "http://images.example.test/a.jpg" not in message
    assert "https://cdn.example.test/thumb.png" not in message
    assert "https://cdn.example.test/original.jpeg" not in message
    assert "https://gall.dcinside.com/mgallery/board/view/?id=thesingularity&no=1234567" in message
    assert "bad pHash match" in message
    assert "1234567" in message
    assert "테스트 제목" in message
    assert "게시글 열기" in message
    assert "닫기" in message
    assert "확정 테러 해시 DB에 등록" in message


def test_high_alert_invokes_topmost_renderer(monkeypatch) -> None:
    calls: list[tuple[Alert, bool]] = []

    def fake_render(alert: Alert, topmost: bool, on_open_post=None, on_remember_bad_hash=None) -> None:
        calls.append((alert, topmost))

    monkeypatch.setattr("dc_watch.local_popup._render_popup_window", fake_render)

    show_topmost_popup(_alert())

    assert calls == [(_alert(), True)]


def test_package_contains_no_forbidden_notification_or_sound_code() -> None:
    package_dir = Path(__file__).resolve().parents[1] / "dc_watch"
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in package_dir.glob("*.py"))

    forbidden = [
        "winsound",
        "beep",
        "playsound",
        "toast",
        "telegram",
        "discord",
        "webhook",
        "bot_token",
        "chat_id",
    ]
    for token in forbidden:
        assert token not in source
