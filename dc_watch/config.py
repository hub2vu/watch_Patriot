from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class AppConfig:
    gallery_id: str = "thesingularity"
    gallery_type: str = "minor"
    poll_seconds: int = 45
    jitter_seconds: int = 10
    pages_to_scan: int = 1
    scan_existing_on_first_run: bool = True
    max_image_bytes: int = 26_214_400
    phash_threshold: int = 7
    enable_tile_phash: bool = True
    tile_phash_grid_size: int = 3
    tile_phash_threshold: int = 6
    tile_phash_min_matches: int = 1
    enable_nudenet: bool = False
    nude_score_threshold: float = 0.45
    alert_on_analysis_failure: bool = False
    high_popup_enabled: bool = True
    warning_popup_enabled: bool = True
    topmost_popup: bool = True
    suppress_image_urls_in_logs: bool = True
    log_level: str = "INFO"
    database_path: str = "dc_watch.sqlite3"
    use_appdata_dir: bool = False
    _base_dir: Path = field(default_factory=lambda: runtime_base_dir(), repr=False, compare=False)
    _appdata_dir: Path | None = field(default=None, repr=False, compare=False)

    @property
    def data_dir(self) -> Path:
        return self._base_dir

    @property
    def config_file(self) -> Path:
        return self._base_dir / "config.toml"

    @property
    def database_file(self) -> Path:
        path = Path(self.database_path)
        if path.is_absolute():
            return path
        return self._base_dir / path

    @property
    def log_dir(self) -> Path:
        return self._base_dir / "logs"


def runtime_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == "cli" and (exe_dir.parent / "DCWatch.exe").exists():
            return exe_dir.parent
        return exe_dir
    return Path.cwd()


def default_config_path(appdata_dir: Path | None = None) -> Path:
    return runtime_base_dir() / "config.toml"


def load_config(config_path: str | Path | None = None, appdata_dir: str | Path | None = None) -> AppConfig:
    path = Path(config_path) if config_path is not None else _discover_config_path(appdata_dir)
    data: dict[str, Any] = {}
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))

    allowed = {field.name for field in AppConfig.__dataclass_fields__.values() if not field.name.startswith("_")}
    filtered = {key: value for key, value in data.items() if key in allowed}
    filtered["use_appdata_dir"] = False
    config = AppConfig(**filtered)
    config._base_dir = path.parent
    config._appdata_dir = None
    return config


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else config.config_file
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        key: value
        for key, value in asdict(config).items()
        if not key.startswith("_")
    }
    lines = [_toml_line(key, value) for key, value in data.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def ensure_user_files(config: AppConfig) -> None:
    config.config_file.parent.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    if not config.config_file.exists():
        save_config(config, config.config_file)


def _discover_config_path(appdata_dir: Path | None) -> Path:
    local = runtime_base_dir() / "config.toml"
    return local


def _toml_line(key: str, value: object) -> str:
    if isinstance(value, bool):
        encoded = "true" if value else "false"
    elif isinstance(value, str):
        encoded = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    else:
        encoded = str(value)
    return f"{key} = {encoded}"
