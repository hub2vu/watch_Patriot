from pathlib import Path

from dc_watch.cli import build_parser


def test_cli_commands_are_registered_without_test_alert_name() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    for command in [
        "watch",
        "gui",
        "once",
        "remember-post",
        "remember-file",
        "test-popup",
        "stats",
        "install-autostart",
        "uninstall-autostart",
    ]:
        assert command in help_text
    assert "test-alert" not in help_text


def test_packaging_files_exist_and_reference_expected_outputs() -> None:
    root = Path(__file__).resolve().parents[1]

    expected = [
        "packaging/pyinstaller/dc-watch-gui.spec",
        "packaging/pyinstaller/dc-watch-cli.spec",
        "scripts/build_windows.ps1",
        "scripts/build_windows_onefile.ps1",
        "scripts/make_release_zip.ps1",
        "installer/inno/dc-watch.iss",
        ".github/workflows/build-windows.yml",
    ]
    for relative in expected:
        assert (root / relative).exists()

    build_script = (root / "scripts/build_windows.ps1").read_text(encoding="utf-8")
    assert "python -m pytest" in build_script
    assert "DCWatch-" in build_script
    assert "win64.zip" in build_script


def test_pyinstaller_specs_resolve_sources_from_repo_root() -> None:
    root = Path(__file__).resolve().parents[1]

    gui_spec = (root / "packaging/pyinstaller/dc-watch-gui.spec").read_text(encoding="utf-8")
    cli_spec = (root / "packaging/pyinstaller/dc-watch-cli.spec").read_text(encoding="utf-8")
    build_script = (root / "scripts/build_windows.ps1").read_text(encoding="utf-8")

    assert '"dc_watch_gui_launcher.py"' in gui_spec
    assert '"dc_watch_cli_launcher.py"' in cli_spec
    assert "from dc_watch.gui import main" in (root / "packaging/pyinstaller/dc_watch_gui_launcher.py").read_text(encoding="utf-8")
    assert "from dc_watch.cli import main" in (root / "packaging/pyinstaller/dc_watch_cli_launcher.py").read_text(encoding="utf-8")
    assert '("../../config.example.toml", ".")' in gui_spec
    assert '("../../README.md", ".")' in gui_spec
    assert '("../../config.example.toml", ".")' in cli_spec
    assert '("../../README.md", ".")' in cli_spec
    assert 'pathex=["../.."]' in gui_spec
    assert 'pathex=["../.."]' in cli_spec
    assert "Invoke-Native" in build_script


def test_gui_exposes_nudenet_option_and_install_button() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "dc_watch/gui.py").read_text(encoding="utf-8")

    assert "enable_nudenet" in source
    assert "check_or_install_nudenet" in source
    assert "install_nudenet" in source


def test_gui_exposes_bad_hash_manager_delete_action() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "dc_watch/gui.py").read_text(encoding="utf-8")

    assert "show_bad_hash_manager" in source
    assert "delete_bad_hash" in source
    assert "bad hash DB 관리" in source


def test_gui_exposes_local_image_bad_hash_registration() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "dc_watch/gui.py").read_text(encoding="utf-8")

    assert "remember_file" in source
    assert "self.remember_local_image_file" in source
    assert "로컬 이미지 해시 DB 등록" in source
