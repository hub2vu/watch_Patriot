from pathlib import Path


def test_gui_alert_popup_uses_parented_toplevel_queue() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "dc_watch/gui.py").read_text(encoding="utf-8")

    assert "enqueue_parented_popup" in source
    assert "enqueue_popup(alert" not in source
