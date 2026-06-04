from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from importlib import resources
from pathlib import Path
from typing import Any
from typing import Iterable

from .models import Alert, BadHash, ImageHashRecord, Post


BUNDLED_BAD_HASHES_RESOURCE = "bundled_bad_hashes.json"
SKIP_BUNDLED_SEED_ENV = "DCWATCH_SKIP_BUNDLED_BAD_HASH_SEED"

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS seen_posts (
        post_no TEXT PRIMARY KEY,
        title TEXT,
        url TEXT,
        writer TEXT,
        first_seen_at INTEGER,
        scanned_at INTEGER,
        risk TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        post_no TEXT PRIMARY KEY,
        alerted_at INTEGER,
        acknowledged_at INTEGER,
        risk TEXT,
        title TEXT,
        url TEXT,
        writer TEXT,
        image_count INTEGER,
        reasons_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS post_image_hashes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_no TEXT,
        sha256 TEXT,
        phash TEXT,
        crop_hash TEXT,
        orb_descriptor BLOB,
        created_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS post_image_hash_tiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_image_hash_id INTEGER,
        post_no TEXT,
        tile_index INTEGER,
        phash TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bad_hashes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT,
        sha256 TEXT,
        phash TEXT,
        crop_hash TEXT,
        orb_descriptor BLOB,
        variant TEXT DEFAULT 'original',
        source_post_no TEXT,
        source_file TEXT,
        seed_key TEXT,
        added_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bad_hash_tiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bad_hash_id INTEGER,
        tile_index INTEGER,
        phash TEXT
    )
    """,
)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self) -> None:
        with self.connect() as conn:
            for statement in SCHEMA:
                conn.execute(statement)
            _ensure_column(conn, "post_image_hashes", "crop_hash", "TEXT")
            _ensure_column(conn, "post_image_hashes", "orb_descriptor", "BLOB")
            _ensure_column(conn, "bad_hashes", "crop_hash", "TEXT")
            _ensure_column(conn, "bad_hashes", "orb_descriptor", "BLOB")
            _ensure_column(conn, "bad_hashes", "variant", "TEXT DEFAULT 'original'")
            _ensure_column(conn, "bad_hashes", "seed_key", "TEXT")
            conn.commit()
        if not _env_flag(SKIP_BUNDLED_SEED_ENV):
            self.import_bundled_bad_hashes()

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "seen_posts": conn.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0],
                "alerts": conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
                "bad_hashes": conn.execute("SELECT COUNT(*) FROM bad_hashes").fetchone()[0],
            }

    def has_seen_post(self, post_no: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM seen_posts WHERE post_no = ?", (post_no,)).fetchone()
            return row is not None

    def seen_count(self) -> int:
        return self.stats()["seen_posts"]

    def upsert_seen_post(self, post: Post, risk: str = "none") -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO seen_posts(post_no, title, url, writer, first_seen_at, scanned_at, risk)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_no) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    writer = excluded.writer,
                    scanned_at = excluded.scanned_at,
                    risk = excluded.risk
                """,
                (post.post_no, post.title, post.url, post.writer, now, now, risk),
            )
            conn.commit()

    def add_post_image_hashes(self, post_no: str, records: Iterable[ImageHashRecord]) -> None:
        now = _now()
        with self.connect() as conn:
            for record in records:
                cursor = conn.execute(
                    "INSERT INTO post_image_hashes(post_no, sha256, phash, crop_hash, orb_descriptor, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                    (post_no, record.sha256, record.phash, record.crop_hash, record.orb_descriptor, now),
                )
                image_hash_id = int(cursor.lastrowid)
                conn.executemany(
                    "INSERT INTO post_image_hash_tiles(post_image_hash_id, post_no, tile_index, phash) VALUES(?, ?, ?, ?)",
                    [(image_hash_id, post_no, index, phash) for index, phash in enumerate(record.tile_phashes)],
                )
            conn.commit()

    def get_post_image_hashes(self, post_no: str) -> list[ImageHashRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, sha256, phash, crop_hash, orb_descriptor FROM post_image_hashes WHERE post_no = ? ORDER BY id",
                (post_no,),
            ).fetchall()
            tile_rows = conn.execute(
                "SELECT post_image_hash_id, phash FROM post_image_hash_tiles WHERE post_no = ? ORDER BY post_image_hash_id, tile_index",
                (post_no,),
            ).fetchall()
        tiles_by_hash: dict[int, list[str]] = {}
        for row in tile_rows:
            tiles_by_hash.setdefault(int(row["post_image_hash_id"]), []).append(row["phash"])
        return [
            ImageHashRecord(
                sha256=row["sha256"],
                phash=row["phash"],
                crop_hash=row["crop_hash"],
                orb_descriptor=row["orb_descriptor"],
                tile_phashes=tuple(tiles_by_hash.get(int(row["id"]), [])),
            )
            for row in rows
        ]

    def get_post(self, post_no: str) -> Post | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT post_no, title, url, writer FROM alerts WHERE post_no = ?",
                (post_no,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT post_no, title, url, writer FROM seen_posts WHERE post_no = ?",
                    (post_no,),
                ).fetchone()
        if row is None:
            return None
        return Post(
            post_no=row["post_no"],
            title=row["title"] or "",
            url=row["url"] or "",
            writer=row["writer"] or "",
            has_image=True,
        )

    def add_alert(self, alert: Alert) -> None:
        alerted_at = alert.alerted_at or _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts(post_no, alerted_at, acknowledged_at, risk, title, url, writer, image_count, reasons_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_no) DO UPDATE SET
                    alerted_at = excluded.alerted_at,
                    risk = excluded.risk,
                    title = excluded.title,
                    url = excluded.url,
                    writer = excluded.writer,
                    image_count = excluded.image_count,
                    reasons_json = excluded.reasons_json
                """,
                (
                    alert.post_no,
                    alerted_at,
                    alert.acknowledged_at,
                    alert.risk,
                    alert.title,
                    alert.url,
                    alert.writer,
                    alert.image_count,
                    json.dumps(alert.reasons, ensure_ascii=False),
                ),
            )
            conn.commit()

    def has_alert(self, post_no: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM alerts WHERE post_no = ?", (post_no,)).fetchone()
            return row is not None

    def acknowledge_alert(self, post_no: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE alerts SET acknowledged_at = ? WHERE post_no = ?", (_now(), post_no))
            conn.commit()

    def recent_alerts(self, limit: int = 50) -> list[Alert]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT post_no, alerted_at, acknowledged_at, risk, title, url, writer, image_count, reasons_json
                FROM alerts
                ORDER BY alerted_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_alert_from_row(row) for row in rows]

    def add_bad_hash(self, bad_hash: BadHash) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO bad_hashes(label, sha256, phash, crop_hash, orb_descriptor, variant, source_post_no, source_file, seed_key, added_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bad_hash.label,
                    bad_hash.sha256,
                    bad_hash.phash,
                    bad_hash.crop_hash,
                    bad_hash.orb_descriptor,
                    bad_hash.variant,
                    bad_hash.source_post_no,
                    bad_hash.source_file,
                    bad_hash.seed_key,
                    bad_hash.added_at or _now(),
                ),
            )
            bad_hash_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO bad_hash_tiles(bad_hash_id, tile_index, phash) VALUES(?, ?, ?)",
                [(bad_hash_id, index, phash) for index, phash in enumerate(bad_hash.tile_phashes)],
            )
            conn.commit()
            return bad_hash_id

    def remember_post_hashes(self, post_no: str, label: str) -> int:
        records = self.get_post_image_hashes(post_no)
        count = 0
        for record in records:
            self.add_bad_hash(
                BadHash(
                    label=label,
                    sha256=record.sha256,
                    phash=record.phash,
                    crop_hash=record.crop_hash,
                    orb_descriptor=record.orb_descriptor,
                    source_post_no=post_no,
                    tile_phashes=record.tile_phashes,
                )
            )
            count += 1
        return count

    def get_bad_hashes(self) -> list[BadHash]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, label, sha256, phash, crop_hash, orb_descriptor, variant, source_post_no, source_file, seed_key, added_at FROM bad_hashes ORDER BY id"
            ).fetchall()
            tile_rows = conn.execute("SELECT bad_hash_id, phash FROM bad_hash_tiles ORDER BY bad_hash_id, tile_index").fetchall()
        tiles_by_bad_hash: dict[int, list[str]] = {}
        for row in tile_rows:
            tiles_by_bad_hash.setdefault(int(row["bad_hash_id"]), []).append(row["phash"])
        return [
            BadHash(
                id=row["id"],
                label=row["label"],
                sha256=row["sha256"],
                phash=row["phash"],
                crop_hash=row["crop_hash"],
                orb_descriptor=row["orb_descriptor"],
                variant=row["variant"] or "original",
                source_post_no=row["source_post_no"],
                source_file=row["source_file"],
                added_at=row["added_at"],
                seed_key=row["seed_key"],
                tile_phashes=tuple(tiles_by_bad_hash.get(int(row["id"]), [])),
            )
            for row in rows
        ]

    def import_bundled_bad_hashes(self, seed_path: str | Path | None = None) -> int:
        payload = _load_seed_payload(seed_path)
        entries = payload.get("bad_hashes")
        if not isinstance(entries, list):
            return 0

        inserted = 0
        with self.connect() as conn:
            for index, raw_entry in enumerate(entries):
                if not isinstance(raw_entry, dict):
                    continue
                entry = _normalize_seed_entry(raw_entry, index)
                if entry is None:
                    continue
                existing_id = _find_existing_seed_row(conn, entry)
                if existing_id is not None:
                    _mark_existing_seed_row(conn, existing_id, entry)
                    continue

                cursor = conn.execute(
                    """
                    INSERT INTO bad_hashes(
                        label, sha256, phash, crop_hash, orb_descriptor, variant,
                        source_post_no, source_file, seed_key, added_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry["label"],
                        entry["sha256"],
                        entry["phash"],
                        entry["crop_hash"],
                        entry["orb_descriptor"],
                        entry["variant"],
                        entry["source_post_no"],
                        entry["source_file"],
                        entry["seed_key"],
                        entry["added_at"],
                    ),
                )
                bad_hash_id = int(cursor.lastrowid)
                _insert_missing_tiles(conn, bad_hash_id, entry["tile_phashes"])
                inserted += 1
            conn.commit()
        return inserted

    def delete_bad_hash(self, bad_hash_id: int) -> bool:
        with self.connect() as conn:
            exists = conn.execute("SELECT 1 FROM bad_hashes WHERE id = ?", (bad_hash_id,)).fetchone()
            if exists is None:
                return False
            conn.execute("DELETE FROM bad_hash_tiles WHERE bad_hash_id = ?", (bad_hash_id,))
            conn.execute("DELETE FROM bad_hashes WHERE id = ?", (bad_hash_id,))
            conn.commit()
            return True


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def _alert_from_row(row: sqlite3.Row) -> Alert:
    return Alert(
        post_no=row["post_no"],
        title=row["title"] or "",
        url=row["url"] or "",
        writer=row["writer"] or "",
        risk=row["risk"],
        image_count=int(row["image_count"] or 0),
        reasons=json.loads(row["reasons_json"] or "[]"),
        alerted_at=int(row["alerted_at"] or 0),
        acknowledged_at=row["acknowledged_at"],
    )


def _now() -> int:
    return int(time.time())


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_seed_payload(seed_path: str | Path | None) -> dict[str, Any]:
    if seed_path is not None:
        path = Path(seed_path)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        seed_resource = resources.files("dc_watch").joinpath(BUNDLED_BAD_HASHES_RESOURCE)
        if not seed_resource.is_file():
            return {}
        return json.loads(seed_resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, ModuleNotFoundError):
        return {}


def _normalize_seed_entry(entry: dict[str, Any], index: int) -> dict[str, Any] | None:
    label = _clean_text(entry.get("label"))
    if not label:
        return None
    normalized = {
        "seed_key": _clean_text(entry.get("seed_key")) or _fallback_seed_key(entry, index),
        "label": label,
        "sha256": _clean_text(entry.get("sha256")),
        "phash": _clean_text(entry.get("phash")),
        "crop_hash": _clean_text(entry.get("crop_hash")),
        "orb_descriptor": _decode_orb_descriptor(entry.get("orb_descriptor_b64")),
        "variant": _clean_text(entry.get("variant")) or "original",
        "source_post_no": _clean_text(entry.get("source_post_no")),
        "source_file": _sanitize_source_file(entry.get("source_file")),
        "added_at": _safe_int(entry.get("added_at")) or _now(),
        "tile_phashes": tuple(_clean_text(item) for item in entry.get("tile_phashes", []) if _clean_text(item)),
    }
    if not any((normalized["sha256"], normalized["phash"], normalized["crop_hash"], normalized["orb_descriptor"], normalized["tile_phashes"])):
        return None
    return normalized


def _fallback_seed_key(entry: dict[str, Any], index: int) -> str:
    parts = [
        _clean_text(entry.get("label")) or "",
        _clean_text(entry.get("sha256")) or "",
        _clean_text(entry.get("phash")) or "",
        _clean_text(entry.get("crop_hash")) or "",
        _clean_text(entry.get("variant")) or "original",
        str(index),
    ]
    return "bundled:" + "|".join(parts)


def _find_existing_seed_row(conn: sqlite3.Connection, entry: dict[str, Any]) -> int | None:
    seed_key = entry["seed_key"]
    if seed_key:
        row = conn.execute("SELECT id FROM bad_hashes WHERE seed_key = ? LIMIT 1", (seed_key,)).fetchone()
        if row is not None:
            return int(row["id"])

    row = conn.execute(
        """
        SELECT id FROM bad_hashes
        WHERE COALESCE(label, '') = ?
          AND COALESCE(sha256, '') = ?
          AND COALESCE(phash, '') = ?
          AND COALESCE(crop_hash, '') = ?
          AND COALESCE(variant, 'original') = ?
        LIMIT 1
        """,
        (
            entry["label"] or "",
            entry["sha256"] or "",
            entry["phash"] or "",
            entry["crop_hash"] or "",
            entry["variant"] or "original",
        ),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def _mark_existing_seed_row(conn: sqlite3.Connection, bad_hash_id: int, entry: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE bad_hashes
        SET seed_key = COALESCE(seed_key, ?),
            crop_hash = COALESCE(crop_hash, ?),
            orb_descriptor = COALESCE(orb_descriptor, ?),
            source_post_no = COALESCE(source_post_no, ?),
            source_file = CASE WHEN ? IS NOT NULL THEN ? ELSE source_file END
        WHERE id = ?
        """,
        (
            entry["seed_key"],
            entry["crop_hash"],
            entry["orb_descriptor"],
            entry["source_post_no"],
            entry["source_file"],
            entry["source_file"],
            bad_hash_id,
        ),
    )
    _insert_missing_tiles(conn, bad_hash_id, entry["tile_phashes"])


def _insert_missing_tiles(conn: sqlite3.Connection, bad_hash_id: int, tile_phashes: Iterable[str]) -> None:
    existing = {
        (int(row["tile_index"]), row["phash"])
        for row in conn.execute("SELECT tile_index, phash FROM bad_hash_tiles WHERE bad_hash_id = ?", (bad_hash_id,)).fetchall()
    }
    rows = [(bad_hash_id, index, phash) for index, phash in enumerate(tile_phashes) if (index, phash) not in existing]
    if rows:
        conn.executemany("INSERT INTO bad_hash_tiles(bad_hash_id, tile_index, phash) VALUES(?, ?, ?)", rows)


def _decode_orb_descriptor(value: Any) -> bytes | None:
    if not value:
        return None
    try:
        return base64.b64decode(str(value), validate=True)
    except (ValueError, TypeError):
        return None


def _sanitize_source_file(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    filename = text.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or filename in {".", ".."}:
        return None
    return filename


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
