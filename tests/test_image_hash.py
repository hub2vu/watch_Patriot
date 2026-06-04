from io import BytesIO

from PIL import Image, ImageDraw

from dc_watch.config import AppConfig
from dc_watch.image_scan import (
    build_bad_hash_records_from_file,
    compute_crop_resistant_hash,
    compute_phash,
    compute_sha256,
    compute_tile_phashes,
    generate_image_variants,
    phash_distance,
    scan_image,
    scan_post_images,
)
from dc_watch.models import BadHash


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (64, 64), color)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_compute_sha256_and_phash_are_stable() -> None:
    data = _png_bytes((12, 34, 56))

    assert compute_sha256(data) == compute_sha256(data)
    assert len(compute_sha256(data)) == 64
    assert compute_phash(data) == compute_phash(data)
    assert len(compute_phash(data)) == 16


def test_phash_distance_counts_hamming_distance() -> None:
    assert phash_distance("0000000000000000", "0000000000000000") == 0
    assert phash_distance("0000000000000000", "0000000000000001") == 1
    assert phash_distance("ffffffffffffffff", "0000000000000000") == 64


def test_scan_image_marks_bad_sha256_as_high() -> None:
    data = _png_bytes((200, 20, 20))
    bad = BadHash(label="known", sha256=compute_sha256(data), phash=None)

    result = scan_image(data, [bad], AppConfig())

    assert result.risk == "high"
    assert any("SHA-256" in reason for reason in result.reasons)


def test_scan_image_marks_similar_phash_as_high() -> None:
    data = _png_bytes((20, 20, 200))
    bad = BadHash(label="known", sha256=None, phash=compute_phash(data))

    result = scan_image(data, [bad], AppConfig(phash_threshold=7))

    assert result.risk == "high"
    assert any("pHash" in reason for reason in result.reasons)


def test_scan_image_suppresses_borderline_phash_without_corroboration(monkeypatch) -> None:
    data = _png_bytes((20, 20, 200))
    bad = BadHash(label="known", sha256=None, phash="0000000000000000")

    monkeypatch.setattr("dc_watch.image_scan.compute_phash", lambda _data: "000000000000001f")

    result = scan_image(
        data,
        [bad],
        AppConfig(
            phash_strict_threshold=4,
            phash_threshold=7,
            enable_tile_phash=False,
            enable_crop_resistant_hash=False,
            enable_orb_matching=False,
        ),
    )

    assert result.risk == "none"
    assert not any("pHash" in reason for reason in result.reasons)


def test_scan_image_marks_borderline_phash_with_crop_same_seed_as_high(monkeypatch) -> None:
    data = _png_bytes((20, 20, 200))
    bad = BadHash(label="known", seed_key="seed-a", sha256=None, phash="0000000000000000", crop_hash="known-crop")

    monkeypatch.setattr("dc_watch.image_scan.compute_phash", lambda _data: "000000000000001f")
    monkeypatch.setattr("dc_watch.image_scan.compute_crop_resistant_hash", lambda _data: "candidate-crop")
    monkeypatch.setattr("dc_watch.image_scan.crop_hash_matches", lambda _candidate, _known, _regions, _hamming: True)

    result = scan_image(
        data,
        [bad],
        AppConfig(
            phash_strict_threshold=4,
            phash_threshold=7,
            enable_tile_phash=False,
            enable_crop_resistant_hash=True,
            enable_orb_matching=False,
        ),
    )

    assert result.risk == "high"
    assert any("borderline pHash" in reason for reason in result.reasons)
    assert any("crop-resistant" in reason for reason in result.reasons)


def test_scan_post_images_aggregates_image_hashes() -> None:
    data = _png_bytes((0, 120, 0))
    result = scan_post_images([data], [], AppConfig())

    assert result.risk == "none"
    assert result.image_count == 1
    assert result.image_hashes[0].sha256 == compute_sha256(data)


def test_scan_image_only_calls_nudenet_when_enabled(monkeypatch) -> None:
    data = _png_bytes((12, 120, 90))
    calls: list[float] = []

    def fake_detect_nudity(_data: bytes, threshold: float) -> list[str]:
        calls.append(threshold)
        return ["NudeNet exposed label: EXPOSED_TEST score=0.99"]

    monkeypatch.setattr("dc_watch.image_scan.detect_nudity", fake_detect_nudity)

    disabled = scan_image(data, [], AppConfig(enable_nudenet=False, nude_score_threshold=0.66))
    enabled = scan_image(data, [], AppConfig(enable_nudenet=True, nude_score_threshold=0.66))

    assert disabled.risk == "none"
    assert enabled.risk == "high"
    assert calls == [0.66]


def test_scan_image_suppresses_single_tile_phash_match(monkeypatch) -> None:
    data = _pattern_grid_png_bytes()
    bad = BadHash(label="known_crop_source", sha256=None, phash=None, tile_phashes=("1111111111111111",))

    monkeypatch.setattr("dc_watch.image_scan.compute_phash", lambda _data: "0000000000000000")
    monkeypatch.setattr("dc_watch.image_scan.compute_tile_phashes", lambda _data, _grid_size=3: ["1111111111111111"])

    result = scan_image(data, [bad], AppConfig(enable_tile_phash=True, tile_phash_threshold=0, tile_phash_min_matches=1))

    assert result.risk == "none"
    assert not any("tile pHash" in reason for reason in result.reasons)


def test_scan_image_marks_multiple_tile_phash_matches_as_high(monkeypatch) -> None:
    data = _pattern_grid_png_bytes()
    bad = BadHash(
        label="known_crop_source",
        sha256=None,
        phash=None,
        tile_phashes=("1111111111111111", "2222222222222222"),
    )

    monkeypatch.setattr("dc_watch.image_scan.compute_phash", lambda _data: "0000000000000000")
    monkeypatch.setattr("dc_watch.image_scan.compute_tile_phashes", lambda _data, _grid_size=3: ["1111111111111111", "2222222222222222"])

    result = scan_image(data, [bad], AppConfig(enable_tile_phash=True, tile_phash_threshold=0, tile_phash_min_matches=1))

    assert result.risk == "high"
    assert any("tile pHash" in reason for reason in result.reasons)


def test_generate_image_variants_includes_rotations_and_flips() -> None:
    variants = generate_image_variants(_pattern_grid_png_bytes())
    names = {variant.name for variant in variants}

    assert {
        "original",
        "rotate_90",
        "rotate_180",
        "rotate_270",
        "flip_left_right",
        "flip_left_right_rotate_90",
        "flip_left_right_rotate_180",
        "flip_left_right_rotate_270",
    }.issubset(names)
    assert len(variants) >= 8


def test_scan_image_suppresses_standalone_crop_resistant_hash_match() -> None:
    original = _pattern_grid_png_bytes()
    crop = _crop_png_bytes(original, box=(0, 0, 64, 64))
    bad = BadHash(label="known_crop_source", crop_hash=compute_crop_resistant_hash(original))

    result = scan_image(
        crop,
        [bad],
        AppConfig(
            enable_crop_resistant_hash=True,
            enable_orb_matching=False,
            crop_hash_hamming_cutoff=16,
            crop_hash_region_cutoff=1,
        ),
    )

    assert result.risk == "none"
    assert not any("crop-resistant" in reason for reason in result.reasons)


def test_scan_image_suppresses_standalone_orb_feature_match(monkeypatch) -> None:
    data = _pattern_grid_png_bytes()
    bad = BadHash(label="known_orb_source", orb_descriptor=b"known")

    monkeypatch.setattr("dc_watch.image_scan.compute_orb_descriptor", lambda _data, _max_features=500: b"candidate")
    monkeypatch.setattr("dc_watch.image_scan.orb_match_count", lambda _candidate, _known, _distance_threshold=64: 99)

    result = scan_image(data, [bad], AppConfig(enable_crop_resistant_hash=False, enable_orb_matching=True, orb_min_matches=65))

    assert result.risk == "none"
    assert not any("ORB" in reason for reason in result.reasons)


def test_scan_image_marks_crop_resistant_and_orb_together_as_high(monkeypatch) -> None:
    original = _pattern_grid_png_bytes()
    crop = _crop_png_bytes(original, box=(0, 0, 64, 64))
    bad = BadHash(label="known_crop_source", crop_hash=compute_crop_resistant_hash(original), orb_descriptor=b"known")

    monkeypatch.setattr("dc_watch.image_scan.compute_orb_descriptor", lambda _data, _max_features=500: b"candidate")
    monkeypatch.setattr("dc_watch.image_scan.orb_match_count", lambda _candidate, _known, _distance_threshold=64: 99)

    result = scan_image(
        crop,
        [bad],
        AppConfig(
            enable_crop_resistant_hash=True,
            enable_orb_matching=True,
            crop_hash_hamming_cutoff=16,
            crop_hash_region_cutoff=1,
            orb_min_matches=65,
        ),
    )

    assert result.risk == "high"
    assert any("crop-resistant" in reason for reason in result.reasons)
    assert any("ORB" in reason for reason in result.reasons)


def test_scan_image_does_not_combine_weak_signals_from_different_seeds(monkeypatch) -> None:
    data = _pattern_grid_png_bytes()
    crop_bad = BadHash(label="known_a", seed_key="seed-a", crop_hash="known-crop")
    orb_bad = BadHash(label="known_b", seed_key="seed-b", orb_descriptor=b"known")

    monkeypatch.setattr("dc_watch.image_scan.compute_crop_resistant_hash", lambda _data: "candidate-crop")
    monkeypatch.setattr("dc_watch.image_scan.crop_hash_matches", lambda _candidate, known, _regions, _hamming: known == "known-crop")
    monkeypatch.setattr("dc_watch.image_scan.compute_orb_descriptor", lambda _data, _max_features=500: b"candidate")
    monkeypatch.setattr("dc_watch.image_scan.orb_match_count", lambda _candidate, _known, _distance_threshold=64: 99)

    result = scan_image(
        data,
        [crop_bad, orb_bad],
        AppConfig(enable_crop_resistant_hash=True, enable_orb_matching=True, orb_min_matches=65),
    )

    assert result.risk == "none"
    assert not result.reasons


def test_scan_image_combines_weak_signals_across_variants_with_same_seed(monkeypatch) -> None:
    data = _pattern_grid_png_bytes()
    crop_bad = BadHash(label="known_a", seed_key="seed-a", variant="original", crop_hash="known-crop")
    orb_bad = BadHash(label="known_a", seed_key="seed-a", variant="rotate_90", orb_descriptor=b"known")

    monkeypatch.setattr("dc_watch.image_scan.compute_crop_resistant_hash", lambda _data: "candidate-crop")
    monkeypatch.setattr("dc_watch.image_scan.crop_hash_matches", lambda _candidate, known, _regions, _hamming: known == "known-crop")
    monkeypatch.setattr("dc_watch.image_scan.compute_orb_descriptor", lambda _data, _max_features=500: b"candidate")
    monkeypatch.setattr("dc_watch.image_scan.orb_match_count", lambda _candidate, _known, _distance_threshold=64: 99)

    result = scan_image(
        data,
        [crop_bad, orb_bad],
        AppConfig(enable_crop_resistant_hash=True, enable_orb_matching=True, orb_min_matches=65),
    )

    assert result.risk == "high"
    assert any("crop-resistant" in reason for reason in result.reasons)
    assert any("ORB" in reason for reason in result.reasons)


def test_build_bad_hash_records_from_file_stores_variant_crop_and_tile_hashes(tmp_path) -> None:
    image_path = tmp_path / "bad.png"
    image_path.write_bytes(_pattern_grid_png_bytes())

    records = build_bad_hash_records_from_file(image_path, "known", AppConfig(enable_tile_phash=True, enable_crop_resistant_hash=True))

    assert len(records) >= 8
    assert {record.variant for record in records} >= {"original", "rotate_90", "flip_left_right"}
    assert all(record.source_file == str(image_path) for record in records)
    assert all(record.crop_hash for record in records)
    assert all(record.tile_phashes for record in records)


def _pattern_grid_png_bytes() -> bytes:
    image = Image.new("RGB", (96, 96), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for row in range(3):
        for col in range(3):
            left = col * 32
            top = row * 32
            base = ((row * 73 + col * 41) % 255, (row * 31 + col * 89) % 255, (row * 109 + col * 23) % 255)
            draw.rectangle((left, top, left + 31, top + 31), fill=base)
            draw.line((left, top, left + 31, top + 31), fill=(255 - base[0], 255 - base[1], 255 - base[2]), width=3)
            draw.ellipse((left + 8, top + 5, left + 24, top + 23), outline=(base[2], base[0], base[1]), width=2)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _crop_png_bytes(data: bytes, box: tuple[int, int, int, int]) -> bytes:
    with Image.open(BytesIO(data)) as image:
        crop = image.crop(box)
        stream = BytesIO()
        crop.save(stream, format="PNG")
        return stream.getvalue()
