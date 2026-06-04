from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from io import BytesIO
from typing import Iterable

import imagehash
from PIL import Image, UnidentifiedImageError

from .config import AppConfig
from .models import BadHash, ImageHashRecord, ImageScanResult, PostScanResult, Risk

log = logging.getLogger(__name__)

NUDE_KEYWORDS = ("EXPOSED", "GENITALIA", "BREAST", "BUTTOCKS", "ANUS", "VAGINA", "PENIS")
_DETECTOR = None
_DETECTOR_READY: bool | None = None


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_phash(data: bytes) -> str:
    with Image.open(BytesIO(data)) as image:
        return str(imagehash.phash(image.convert("RGB")))


def compute_tile_phashes(data: bytes, grid_size: int = 3) -> list[str]:
    grid = max(1, int(grid_size))
    with Image.open(BytesIO(data)) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        if width < grid or height < grid:
            return [str(imagehash.phash(rgb))]
        hashes: list[str] = []
        for row in range(grid):
            top = row * height // grid
            bottom = (row + 1) * height // grid
            for col in range(grid):
                left = col * width // grid
                right = (col + 1) * width // grid
                tile = rgb.crop((left, top, right, bottom))
                hashes.append(str(imagehash.phash(tile)))
        return hashes


def phash_distance(a: str, b: str) -> int:
    left = int(a, 16)
    right = int(b, 16)
    return (left ^ right).bit_count()


def scan_image(data: bytes, bad_hashes: list[BadHash], config: AppConfig) -> ImageScanResult:
    sha = compute_sha256(data)
    reasons: list[str] = []
    risk: Risk = "none"
    try:
        phash = compute_phash(data)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        phash = None
        if config.alert_on_analysis_failure:
            risk = "warning"
            reasons.append(f"image analysis failed: {exc.__class__.__name__}")

    tile_phashes: tuple[str, ...] = ()
    if phash and config.enable_tile_phash:
        try:
            tile_phashes = tuple(compute_tile_phashes(data, config.tile_phash_grid_size))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            if config.alert_on_analysis_failure:
                risk = "warning"
                reasons.append(f"tile pHash analysis failed: {exc.__class__.__name__}")

    for bad_hash in bad_hashes:
        if bad_hash.sha256 and bad_hash.sha256.lower() == sha.lower():
            risk = "high"
            reasons.append(f"bad SHA-256 match: {bad_hash.label}")
        if phash and bad_hash.phash:
            try:
                distance = phash_distance(phash, bad_hash.phash)
            except ValueError:
                continue
            if distance <= config.phash_threshold:
                risk = "high"
                reasons.append(f"bad pHash match: {bad_hash.label} distance={distance}")
        if config.enable_tile_phash and phash and bad_hash.tile_phashes:
            match_count = _tile_phash_match_count(
                [phash, *tile_phashes],
                bad_hash.tile_phashes,
                config.tile_phash_threshold,
            )
            if match_count >= max(1, config.tile_phash_min_matches):
                risk = "high"
                reasons.append(f"bad tile pHash match: {bad_hash.label} matches={match_count}")

    if config.enable_nudenet:
        nude_reasons = detect_nudity(data, config.nude_score_threshold)
        if nude_reasons:
            risk = "high"
            reasons.extend(nude_reasons)

    return ImageScanResult(sha256=sha, phash=phash, risk=risk, reasons=_dedupe_reasons(reasons), tile_phashes=tile_phashes)


def scan_post_images(image_bytes_list: Iterable[bytes], bad_hashes: list[BadHash], config: AppConfig) -> PostScanResult:
    image_hashes: list[ImageHashRecord] = []
    reasons: list[str] = []
    risk: Risk = "none"
    count = 0
    for data in image_bytes_list:
        count += 1
        result = scan_image(data, bad_hashes, config)
        image_hashes.append(ImageHashRecord(sha256=result.sha256, phash=result.phash, tile_phashes=result.tile_phashes))
        reasons.extend(result.reasons)
        risk = _max_risk(risk, result.risk)
    return PostScanResult(risk=risk, reasons=_dedupe_reasons(reasons), image_count=count, image_hashes=image_hashes)


def detect_nudity(data: bytes, threshold: float) -> list[str]:
    detector = _get_detector()
    if detector is None:
        return []
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp:
            temp.write(data)
            temp_path = temp.name
        raw = detector.detect(temp_path)
    except Exception as exc:  # pragma: no cover - optional model depends on local install
        log.warning("NudeNet detection failed: %s", exc.__class__.__name__)
        return []
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    reasons: list[str] = []
    for item in raw or []:
        label = str(item.get("class") or item.get("label") or "").upper()
        score = float(item.get("score") or item.get("confidence") or 0.0)
        if score >= threshold and any(keyword in label for keyword in NUDE_KEYWORDS):
            reasons.append(f"NudeNet exposed label: {label} score={score:.2f}")
    return reasons


def _get_detector():
    global _DETECTOR, _DETECTOR_READY
    if _DETECTOR_READY is False:
        return None
    if _DETECTOR_READY is True:
        return _DETECTOR
    try:
        from nudenet import NudeDetector

        _DETECTOR = NudeDetector()
        _DETECTOR_READY = True
        log.info("NudeNet detector enabled")
        return _DETECTOR
    except Exception as exc:
        _DETECTOR_READY = False
        log.info("NudeNet detector disabled: %s", exc.__class__.__name__)
        return None


def reset_nudenet_detector() -> None:
    global _DETECTOR, _DETECTOR_READY
    _DETECTOR = None
    _DETECTOR_READY = None


def _max_risk(left: Risk, right: Risk) -> Risk:
    order = {"none": 0, "warning": 1, "high": 2}
    return left if order[left] >= order[right] else right


def _tile_phash_match_count(candidate_phashes: Iterable[str], bad_tile_phashes: Iterable[str], threshold: int) -> int:
    used_bad_indexes: set[int] = set()
    bad_tiles = list(bad_tile_phashes)
    for candidate in candidate_phashes:
        for index, bad_tile in enumerate(bad_tiles):
            if index in used_bad_indexes:
                continue
            try:
                distance = phash_distance(candidate, bad_tile)
            except ValueError:
                continue
            if distance <= threshold:
                used_bad_indexes.add(index)
                break
    return len(used_bad_indexes)


def _dedupe_reasons(reasons: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            result.append(reason)
    return result
