from io import BytesIO
from pathlib import Path

from PIL import Image

from dc_watch.cli import remember_post
from dc_watch.config import AppConfig
from dc_watch.db import Database
from dc_watch.models import ImageHashRecord, Post


def test_remember_post_redownloads_images_and_registers_enhanced_variants(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    post = Post(post_no="123", title="sample", url="https://example.test/post/123", writer="writer", has_image=True)
    db.upsert_seen_post(post)

    monkeypatch.setattr("dc_watch.cli.fetch_post_image_urls", lambda _url: ["internal-image-url"])
    monkeypatch.setattr("dc_watch.cli.download_image_bytes", lambda _url, referer, max_bytes: _png_bytes())

    ids = remember_post(db, "123", "known_attack", AppConfig(enable_orb_matching=False))
    loaded = db.get_bad_hashes()

    assert len(ids) == 8
    assert len(loaded) == 8
    assert {item.variant for item in loaded} >= {"original", "rotate_90", "flip_left_right"}
    assert all(item.source_post_no == "123" for item in loaded)
    assert all(item.crop_hash for item in loaded)
    assert all(item.tile_phashes for item in loaded)


def test_remember_post_falls_back_to_stored_hashes_when_redownload_fails(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    post = Post(post_no="123", title="sample", url="https://example.test/post/123", writer="writer", has_image=True)
    db.upsert_seen_post(post)
    db.add_post_image_hashes("123", [ImageHashRecord(sha256="a" * 64, phash="b" * 16, tile_phashes=("1" * 16,))])

    def fail_fetch(_url: str) -> list[str]:
        raise RuntimeError("network disabled in test")

    monkeypatch.setattr("dc_watch.cli.fetch_post_image_urls", fail_fetch)

    ids = remember_post(db, "123", "known_attack", AppConfig())

    assert len(ids) == 1
    assert db.get_bad_hashes()[0].tile_phashes == ("1" * 16,)


def _png_bytes() -> bytes:
    image = Image.new("RGB", (96, 96), (240, 240, 240))
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()
