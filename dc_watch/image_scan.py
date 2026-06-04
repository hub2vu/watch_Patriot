from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import AppConfig
from .models import BadHash, ImageHashRecord, ImageScanResult, PostScanResult, Risk

log = logging.getLogger(__name__)

NUDE_KEYWORDS = ("EXPOSED", "GENITALIA", "BREAST", "BUTTOCKS", "ANUS", "VAGINA", "PENIS")
_DETECTOR = None
_DETECTOR_READY: bool | None = None


@dataclass(frozen=True)
class ImageVariant:
    name: str
    data: bytes


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


def compute_crop_resistant_hash(data: bytes) -> str:
    with Image.open(BytesIO(data)) as image:
        return str(imagehash.crop_resistant_hash(image.convert("RGB")))


def crop_hash_matches(candidate_hash: str, bad_hash: str, region_cutoff: int = 1, hamming_cutoff: int = 16) -> bool:
    candidate = imagehash.hex_to_multihash(candidate_hash)
    known = imagehash.hex_to_multihash(bad_hash)
    return bool(candidate.matches(known, region_cutoff=max(1, region_cutoff), hamming_cutoff=max(0, hamming_cutoff)))


def compute_orb_descriptor(data: bytes, max_features: int = 500) -> bytes | None:
    cv2 = _get_cv2()
    if cv2 is None:
        return None
    try:
        import numpy as np

        array = np.frombuffer(data, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
        if image is None:
            return None
        orb = cv2.ORB_create(nfeatures=max(1, int(max_features)))
        _keypoints, descriptors = orb.detectAndCompute(image, None)
        if descriptors is None or len(descriptors) == 0:
            return None
        stream = BytesIO()
        np.save(stream, descriptors.astype("uint8", copy=False), allow_pickle=False)
        return stream.getvalue()
    except Exception as exc:  # pragma: no cover - depends on optional OpenCV install
        log.info("ORB descriptor disabled for image: %s", exc.__class__.__name__)
        return None


def orb_match_count(candidate_descriptor: bytes | None, bad_descriptor: bytes | None, distance_threshold: int = 64) -> int:
    if not candidate_descriptor or not bad_descriptor:
        return 0
    cv2 = _get_cv2()
    if cv2 is None:
        return 0
    try:
        import numpy as np

        candidate = np.load(BytesIO(candidate_descriptor), allow_pickle=False)
        known = np.load(BytesIO(bad_descriptor), allow_pickle=False)
        if candidate.size == 0 or known.size == 0:
            return 0
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = matcher.match(candidate.astype("uint8", copy=False), known.astype("uint8", copy=False))
        return sum(1 for match in matches if match.distance <= max(0, distance_threshold))
    except Exception as exc:  # pragma: no cover - depends on optional OpenCV install
        log.info("ORB descriptor comparison skipped: %s", exc.__class__.__name__)
        return 0


def phash_distance(a: str, b: str) -> int:
    left = int(a, 16)
    right = int(b, 16)
    return (left ^ right).bit_count()


def scan_image(data: bytes, bad_hashes: list[BadHash], config: AppConfig) -> ImageScanResult:
    sha = compute_sha256(data)
    reasons: list[str] = []
    risk: Risk = "none"
    crop_hash: str | None = None
    orb_descriptor: bytes | None = None
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

    if config.enable_crop_resistant_hash:
        try:
            crop_hash = compute_crop_resistant_hash(data)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            if config.alert_on_analysis_failure:
                risk = "warning"
                reasons.append(f"crop-resistant hash analysis failed: {exc.__class__.__name__}")

    if config.enable_orb_matching:
        orb_descriptor = compute_orb_descriptor(data, config.orb_max_features)

    for bad_hash in bad_hashes:
        bad_label = _bad_hash_display_label(bad_hash)
        if bad_hash.sha256 and bad_hash.sha256.lower() == sha.lower():
            risk = "high"
            reasons.append(f"bad SHA-256 match: {bad_label}")
        if phash and bad_hash.phash:
            try:
                distance = phash_distance(phash, bad_hash.phash)
            except ValueError:
                continue
            if distance <= config.phash_threshold:
                risk = "high"
                reasons.append(f"bad pHash match: {bad_label} distance={distance}")
        if config.enable_tile_phash and phash and bad_hash.tile_phashes:
            match_count = _tile_phash_match_count(
                [phash, *tile_phashes],
                bad_hash.tile_phashes,
                config.tile_phash_threshold,
            )
            if match_count >= max(1, config.tile_phash_min_matches):
                risk = "high"
                reasons.append(f"bad tile pHash match: {bad_label} matches={match_count}")
        if config.enable_crop_resistant_hash and crop_hash and bad_hash.crop_hash:
            try:
                if crop_hash_matches(crop_hash, bad_hash.crop_hash, config.crop_hash_region_cutoff, config.crop_hash_hamming_cutoff):
                    risk = "high"
                    reasons.append(f"bad crop-resistant hash match: {bad_label}")
            except (ValueError, TypeError):
                continue
        if config.enable_orb_matching and orb_descriptor and bad_hash.orb_descriptor:
            matches = orb_match_count(orb_descriptor, bad_hash.orb_descriptor, config.orb_distance_threshold)
            if matches >= max(1, config.orb_min_matches):
                risk = "high"
                reasons.append(f"bad ORB feature match: {bad_label} matches={matches}")

    if config.enable_nudenet:
        nude_reasons = detect_nudity(data, config.nude_score_threshold)
        if nude_reasons:
            risk = "high"
            reasons.extend(nude_reasons)

    return ImageScanResult(
        sha256=sha,
        phash=phash,
        risk=risk,
        reasons=_dedupe_reasons(reasons),
        crop_hash=crop_hash,
        orb_descriptor=orb_descriptor,
        tile_phashes=tile_phashes,
    )


def scan_post_images(image_bytes_list: Iterable[bytes], bad_hashes: list[BadHash], config: AppConfig) -> PostScanResult:
    image_hashes: list[ImageHashRecord] = []
    reasons: list[str] = []
    risk: Risk = "none"
    count = 0
    for data in image_bytes_list:
        count += 1
        result = scan_image(data, bad_hashes, config)
        image_hashes.append(
            ImageHashRecord(
                sha256=result.sha256,
                phash=result.phash,
                crop_hash=result.crop_hash,
                orb_descriptor=result.orb_descriptor,
                tile_phashes=result.tile_phashes,
            )
        )
        reasons.extend(result.reasons)
        risk = _max_risk(risk, result.risk)
    return PostScanResult(risk=risk, reasons=_dedupe_reasons(reasons), image_count=count, image_hashes=image_hashes)


def generate_image_variants(data: bytes) -> list[ImageVariant]:
    with Image.open(BytesIO(data)) as image:
        base = ImageOps.exif_transpose(image).convert("RGB")
        variants = [
            ("original", base),
            ("rotate_90", base.transpose(Image.Transpose.ROTATE_90)),
            ("rotate_180", base.transpose(Image.Transpose.ROTATE_180)),
            ("rotate_270", base.transpose(Image.Transpose.ROTATE_270)),
            ("flip_left_right", base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)),
            ("flip_left_right_rotate_90", base.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(Image.Transpose.ROTATE_90)),
            ("flip_left_right_rotate_180", base.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(Image.Transpose.ROTATE_180)),
            ("flip_left_right_rotate_270", base.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(Image.Transpose.ROTATE_270)),
        ]
        return [ImageVariant(name=name, data=_image_to_png_bytes(variant)) for name, variant in variants]


def build_bad_hash_records_from_file(image_path: Path, label: str, config: AppConfig) -> list[BadHash]:
    source_data = image_path.read_bytes()
    records: list[BadHash] = []
    for variant in generate_image_variants(source_data):
        sha = compute_sha256(variant.data)
        try:
            phash = compute_phash(variant.data)
        except Exception:
            phash = None
        tile_phashes: tuple[str, ...] = ()
        if phash and config.enable_tile_phash:
            try:
                tile_phashes = tuple(compute_tile_phashes(variant.data, config.tile_phash_grid_size))
            except Exception:
                tile_phashes = ()
        crop_hash: str | None = None
        if config.enable_crop_resistant_hash:
            try:
                crop_hash = compute_crop_resistant_hash(variant.data)
            except Exception:
                crop_hash = None
        orb_descriptor = compute_orb_descriptor(variant.data, config.orb_max_features) if config.enable_orb_matching else None
        records.append(
            BadHash(
                label=label,
                sha256=sha,
                phash=phash,
                crop_hash=crop_hash,
                orb_descriptor=orb_descriptor,
                variant=variant.name,
                source_file=str(image_path),
                tile_phashes=tile_phashes,
            )
        )
    return records


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


def is_orb_available() -> bool:
    return _get_cv2() is not None


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


def _image_to_png_bytes(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _bad_hash_display_label(bad_hash: BadHash) -> str:
    if bad_hash.variant and bad_hash.variant != "original":
        return f"{bad_hash.label}/{bad_hash.variant}"
    return bad_hash.label


def _get_cv2():
    try:
        import cv2

        return cv2
    except Exception:
        return None


def _dedupe_reasons(reasons: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            result.append(reason)
    return result
