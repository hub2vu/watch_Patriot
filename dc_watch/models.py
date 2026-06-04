from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Risk = Literal["none", "warning", "high"]


@dataclass(frozen=True)
class Post:
    post_no: str
    title: str
    url: str
    writer: str = ""
    has_image: bool = False
    post_type: str = ""


@dataclass(frozen=True)
class BadHash:
    label: str
    sha256: str | None = None
    phash: str | None = None
    crop_hash: str | None = None
    orb_descriptor: bytes | None = None
    variant: str = "original"
    source_post_no: str | None = None
    source_file: str | None = None
    added_at: int = 0
    id: int | None = None
    seed_key: str | None = None
    tile_phashes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ImageHashRecord:
    sha256: str
    phash: str | None
    crop_hash: str | None = None
    orb_descriptor: bytes | None = None
    tile_phashes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ImageScanResult:
    sha256: str
    phash: str | None
    risk: Risk
    reasons: list[str] = field(default_factory=list)
    crop_hash: str | None = None
    orb_descriptor: bytes | None = None
    tile_phashes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PostScanResult:
    risk: Risk
    reasons: list[str]
    image_count: int
    image_hashes: list[ImageHashRecord]


@dataclass(frozen=True)
class Alert:
    post_no: str
    title: str
    url: str
    writer: str
    risk: Risk
    image_count: int
    reasons: list[str]
    alerted_at: int = 0
    acknowledged_at: int | None = None
