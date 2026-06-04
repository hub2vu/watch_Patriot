from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from .models import Alert, BadHash, ImageHashRecord, Post


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
        conn = sqlite3.connect(self.path)
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
            conn.commit()

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
                INSERT INTO bad_hashes(label, sha256, phash, crop_hash, orb_descriptor, variant, source_post_no, source_file, added_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "SELECT id, label, sha256, phash, crop_hash, orb_descriptor, variant, source_post_no, source_file, added_at FROM bad_hashes ORDER BY id"
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
                tile_phashes=tuple(tiles_by_bad_hash.get(int(row["id"]), [])),
            )
            for row in rows
        ]

    def delete_bad_hash(self, bad_hash_id: int) -> bool:
        with self.connect() as conn:
            exists = conn.execute("SELECT 1 FROM bad_hashes WHERE id = ?", (bad_hash_id,)).fetchone()
            if exists is None:
                return False
            conn.execute("DELETE FROM bad_hash_tiles WHERE bad_hash_id = ?", (bad_hash_id,))
            conn.execute("DELETE FROM bad_hashes WHERE id = ?", (bad_hash_id,))
            conn.commit()
            return True


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
