from dc_watch import optional_deps


def test_install_nudenet_refuses_frozen_runtime(monkeypatch) -> None:
    monkeypatch.setattr(optional_deps, "is_nudenet_available", lambda: False)
    monkeypatch.setattr(optional_deps.sys, "frozen", True, raising=False)

    result = optional_deps.install_nudenet()

    assert result.attempted is False
    assert result.installed is False
    assert "PyInstaller" in result.message
