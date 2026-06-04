import base64
import json
import sqlite3
from pathlib import Path

from dc_watch.db import Database
from dc_watch.models import Alert, BadHash, ImageHashRecord, Post


def test_db_migration_creates_tables_and_counts(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()

    counts = db.stats()

    assert counts == {"seen_posts": 0, "alerts": 0, "bad_hashes": 0}


def test_database_context_manager_closes_connection(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")

    with db.connect() as conn:
        conn.execute("SELECT 1")

    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError as exc:
        assert "closed" in str(exc).lower()
    else:
        raise AssertionError("database connection remained open after context manager exit")


def test_seen_posts_alerts_and_bad_hash_registration(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    post = Post(post_no="1234567", title="sample", url="https://gall.dcinside.com/mgallery/board/view/?id=x&no=1234567", writer="writer")
    alert = Alert(
        post_no=post.post_no,
        title=post.title,
        url=post.url,
        writer=post.writer,
        risk="high",
        image_count=1,
        reasons=["bad sha256 match"],
    )

    db.upsert_seen_post(post, risk="high")
    db.add_post_image_hashes(post.post_no, [ImageHashRecord(sha256="a" * 64, phash="f" * 16, tile_phashes=("1" * 16, "2" * 16))])
    db.add_alert(alert)
    inserted = db.remember_post_hashes("1234567", "known_attack_001")

    assert inserted == 1
    assert db.has_seen_post("1234567") is True
    assert db.has_alert("1234567") is True
    assert len(db.get_bad_hashes()) == 1
    assert db.get_bad_hashes()[0].label == "known_attack_001"
    assert db.get_bad_hashes()[0].tile_phashes == ("1" * 16, "2" * 16)
    assert db.stats() == {"seen_posts": 1, "alerts": 1, "bad_hashes": 1}


def test_acknowledge_alert_marks_alert(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    alert = Alert(post_no="1", title="t", url="https://example.com/post", writer="", risk="warning", image_count=0, reasons=["analysis failure"])

    db.add_alert(alert)
    db.acknowledge_alert("1")
    recent = db.recent_alerts(limit=5)

    assert recent[0].acknowledged_at is not None


def test_mark_alert_false_positive_hides_alert_from_recent_default(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    false_positive = Alert(post_no="1", title="fp", url="https://example.com/fp", writer="", risk="high", image_count=1, reasons=["weak match"])
    real_alert = Alert(post_no="2", title="real", url="https://example.com/real", writer="", risk="high", image_count=1, reasons=["sha"])

    db.add_alert(false_positive)
    db.add_alert(real_alert)
    assert db.mark_alert_false_positive("1") is True

    visible = db.recent_alerts(limit=5)
    all_alerts = db.recent_alerts(limit=5, include_false_positives=True)

    assert [alert.post_no for alert in visible] == ["2"]
    marked = {alert.post_no: alert for alert in all_alerts}["1"]
    assert marked.false_positive_at is not None


def test_mark_alert_false_positive_returns_false_for_missing_alert(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()

    assert db.mark_alert_false_positive("missing") is False


def test_delete_bad_hash_removes_tiles_too(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    bad_id = db.add_bad_hash(
        BadHash(
            label="manual_bad",
            sha256="a" * 64,
            phash="b" * 16,
            source_file="bad.jpg",
            tile_phashes=("1" * 16, "2" * 16),
        )
    )

    assert db.delete_bad_hash(bad_id) is True
    assert db.delete_bad_hash(bad_id) is False
    assert db.get_bad_hashes() == []
    assert db.stats()["bad_hashes"] == 0
    with db.connect() as conn:
        tile_count = conn.execute("SELECT COUNT(*) FROM bad_hash_tiles WHERE bad_hash_id = ?", (bad_id,)).fetchone()[0]
    assert tile_count == 0


def test_bad_hash_round_trips_crop_hash_orb_descriptor_and_variant(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()

    bad_id = db.add_bad_hash(
        BadHash(
            label="manual_bad",
            sha256="a" * 64,
            phash="b" * 16,
            source_file="bad.jpg",
            tile_phashes=("1" * 16,),
            crop_hash="c" * 16,
            orb_descriptor=b"orb-bytes",
            variant="rotate_90",
        )
    )

    loaded = db.get_bad_hashes()[0]

    assert loaded.id == bad_id
    assert loaded.crop_hash == "c" * 16
    assert loaded.orb_descriptor == b"orb-bytes"
    assert loaded.variant == "rotate_90"


def test_import_bundled_bad_hashes_round_trips_tiles_and_sanitizes_source_path(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    seed_path = tmp_path / "bundled_bad_hashes.json"
    seed_path.write_text(
        json.dumps(
            {
                "version": 1,
                "bad_hashes": [
                    {
                        "seed_key": "seeded:one",
                        "label": "seeded_attack",
                        "sha256": "a" * 64,
                        "phash": "b" * 16,
                        "crop_hash": "c" * 16,
                        "orb_descriptor_b64": base64.b64encode(b"orb-bytes").decode("ascii"),
                        "variant": "rotate_90",
                        "source_post_no": "123",
                        "source_file": r"C:\Users\hub2v\Downloads\bad.jpg",
                        "added_at": 1234,
                        "tile_phashes": ["1" * 16, "2" * 16],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    inserted = db.import_bundled_bad_hashes(seed_path)
    inserted_again = db.import_bundled_bad_hashes(seed_path)
    loaded = db.get_bad_hashes()

    assert inserted == 1
    assert inserted_again == 0
    assert len(loaded) == 1
    assert loaded[0].label == "seeded_attack"
    assert loaded[0].sha256 == "a" * 64
    assert loaded[0].phash == "b" * 16
    assert loaded[0].crop_hash == "c" * 16
    assert loaded[0].orb_descriptor == b"orb-bytes"
    assert loaded[0].variant == "rotate_90"
    assert loaded[0].source_post_no == "123"
    assert loaded[0].source_file == "bad.jpg"
    assert loaded[0].seed_key == "seeded:one"
    assert loaded[0].tile_phashes == ("1" * 16, "2" * 16)


def test_import_bundled_bad_hashes_skips_existing_equivalent_without_seed_key(tmp_path: Path) -> None:
    db = Database(tmp_path / "watch.sqlite3")
    db.migrate()
    db.add_bad_hash(
        BadHash(
            label="seeded_attack",
            sha256="a" * 64,
            phash="b" * 16,
            crop_hash="c" * 16,
            variant="original",
            source_file=r"C:\Users\hub2v\Downloads\bad.jpg",
            tile_phashes=("1" * 16,),
        )
    )
    seed_path = tmp_path / "bundled_bad_hashes.json"
    seed_path.write_text(
        json.dumps(
            {
                "version": 1,
                "bad_hashes": [
                    {
                        "seed_key": "seeded:equivalent",
                        "label": "seeded_attack",
                        "sha256": "a" * 64,
                        "phash": "b" * 16,
                        "crop_hash": "c" * 16,
                        "variant": "original",
                        "source_file": "bundled_seed",
                        "tile_phashes": ["1" * 16],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    inserted = db.import_bundled_bad_hashes(seed_path)
    loaded = db.get_bad_hashes()

    assert inserted == 0
    assert len(loaded) == 1
    assert loaded[0].seed_key == "seeded:equivalent"
    assert loaded[0].source_file == "bundled_seed"
