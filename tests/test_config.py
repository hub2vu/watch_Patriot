from pathlib import Path
import sys

from dc_watch.config import AppConfig, ensure_user_files, load_config, runtime_base_dir, save_config


def test_default_config_uses_requested_safety_defaults(tmp_path: Path) -> None:
    config = load_config(config_path=tmp_path / "missing.toml", appdata_dir=tmp_path)

    assert config.gallery_id == "thesingularity"
    assert config.gallery_type == "minor"
    assert config.poll_seconds == 45
    assert config.jitter_seconds == 10
    assert config.scan_existing_on_first_run is True
    assert config.max_image_bytes == 26_214_400
    assert config.phash_threshold == 7
    assert config.enable_tile_phash is True
    assert config.tile_phash_grid_size == 3
    assert config.tile_phash_threshold == 6
    assert config.tile_phash_min_matches == 1
    assert config.enable_crop_resistant_hash is True
    assert config.crop_hash_region_cutoff == 1
    assert config.crop_hash_hamming_cutoff == 16
    assert config.enable_orb_matching is True
    assert config.orb_max_features == 500
    assert config.orb_distance_threshold == 64
    assert config.orb_min_matches == 60
    assert config.enable_nudenet is False
    assert config.nude_score_threshold == 0.45
    assert config.alert_on_analysis_failure is False
    assert config.high_popup_enabled is True
    assert config.warning_popup_enabled is True
    assert config.topmost_popup is True
    assert config.suppress_image_urls_in_logs is True
    assert config.use_appdata_dir is False
    assert config.config_file == tmp_path / "config.toml"
    assert config.database_file == tmp_path / "dc_watch.sqlite3"
    assert config.log_dir == tmp_path / "logs"
    assert "telegram" not in config.__dict__
    assert "discord" not in config.__dict__
    assert "sound_enabled" not in config.__dict__


def test_save_and_load_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = AppConfig(
        gallery_id="example",
        poll_seconds=60,
        jitter_seconds=3,
        enable_tile_phash=False,
        tile_phash_grid_size=4,
        tile_phash_threshold=5,
        tile_phash_min_matches=2,
        enable_nudenet=True,
        nude_score_threshold=0.7,
        database_path="local.sqlite3",
    )

    save_config(config, path)
    loaded = load_config(config_path=path, appdata_dir=tmp_path)

    assert loaded.gallery_id == "example"
    assert loaded.poll_seconds == 60
    assert loaded.jitter_seconds == 3
    assert loaded.enable_tile_phash is False
    assert loaded.tile_phash_grid_size == 4
    assert loaded.tile_phash_threshold == 5
    assert loaded.tile_phash_min_matches == 2
    assert loaded.enable_nudenet is True
    assert loaded.nude_score_threshold == 0.7
    assert loaded.database_file == path.parent / "local.sqlite3"


def test_default_config_does_not_create_appdata_when_appdata_env_exists(tmp_path: Path, monkeypatch) -> None:
    appdata = tmp_path / "real-appdata"
    runtime = tmp_path / "portable"
    monkeypatch.setenv("APPDATA", str(appdata))

    config = load_config(config_path=runtime / "missing.toml", appdata_dir=appdata)
    ensure_user_files(config)

    assert config.config_file == runtime / "config.toml"
    assert config.database_file == runtime / "dc_watch.sqlite3"
    assert config.log_dir == runtime / "logs"
    assert (runtime / "config.toml").exists()
    assert (runtime / "logs").is_dir()
    assert not (appdata / "DCWatch").exists()


def test_appdata_mode_setting_is_ignored_for_complete_sandbox(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("use_appdata_dir = true\n", encoding="utf-8")

    config = load_config(config_path=path, appdata_dir=tmp_path / "appdata")

    assert config.use_appdata_dir is False
    assert config.config_file == tmp_path / "config.toml"
    assert config.database_file == tmp_path / "dc_watch.sqlite3"


def test_frozen_cli_uses_parent_app_folder_as_runtime_base(tmp_path: Path, monkeypatch) -> None:
    app_folder = tmp_path / "DCWatch"
    cli_exe = app_folder / "CLI" / "DCWatchCLI.exe"
    cli_exe.parent.mkdir(parents=True)
    (app_folder / "DCWatch.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))

    assert runtime_base_dir() == app_folder
