from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PostedAdjustment:
    acceptance_id: str
    package_id: str
    source_batch: str
    day: str
    region: str
    delta: int


def initialize_postings(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS posted_adjustments (
            acceptance_id TEXT NOT NULL REFERENCES receipts(acceptance_id),
            region TEXT NOT NULL,
            delta TEXT NOT NULL,
            PRIMARY KEY (acceptance_id, region)
        )
    ''')


def append_posted_adjustments(db: sqlite3.Connection, adjustments: Iterable[PostedAdjustment]) -> None:
    if not db.in_transaction:
        raise RuntimeError('posting adjustments require a publication transaction')
    db.executemany('''
        INSERT INTO posted_adjustments (acceptance_id, region, delta) VALUES (?, ?, ?)
    ''', ((item.acceptance_id, item.region, str(item.delta)) for item in adjustments))


def read_posted_adjustments(db: sqlite3.Connection, day: str) -> tuple[PostedAdjustment, ...]:
    rows = db.execute('''
        SELECT r.acceptance_id, r.package_id, r.source_batch, r.day, p.region, p.delta
        FROM receipts AS r JOIN posted_adjustments AS p ON p.acceptance_id = r.acceptance_id
        WHERE r.day = ? ORDER BY r.acceptance_id, p.region
    ''', (day,))
    return tuple(PostedAdjustment(*fields, int(delta)) for *fields, delta in rows)
