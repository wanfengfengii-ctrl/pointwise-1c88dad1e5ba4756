from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping

from intake.batch_identity import canonical_batch_content
from intake.records import append_observations, normalize_observation
from intake.stations import require_identifier
from receive.transaction import write_transaction


class IngestError(ValueError):
    pass


@dataclass(frozen=True)
class SourceBatch:
    batch_id: str
    source_id: str
    content: str


def initialize_batches(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS source_batches (
            batch_id TEXT PRIMARY KEY NOT NULL,
            source_id TEXT NOT NULL,
            content TEXT NOT NULL
        )
    ''')


def find_batch(db: sqlite3.Connection, batch_id: str) -> SourceBatch | None:
    row = db.execute(
        'SELECT batch_id, source_id, content FROM source_batches WHERE batch_id = ?',
        (batch_id,),
    ).fetchone()
    return None if row is None else SourceBatch(*row)


def ingest(
    db: sqlite3.Connection,
    batch_id: str,
    source_id: str,
    rows: Iterable[Mapping[str, object]],
) -> SourceBatch:
    try:
        require_identifier(batch_id, 'batch_id')
        require_identifier(source_id, 'source_id')
        with write_transaction(db):
            readings = tuple(normalize_observation(db, batch_id, source_id, row) for row in rows)
            if len({reading.reading_id for reading in readings}) != len(readings):
                raise ValueError('reading ids must be unique within a batch')
            content = canonical_batch_content(source_id, readings)
            existing = find_batch(db, batch_id)
            if existing is not None:
                if existing.content != content:
                    raise ValueError(f'conflicting content for source batch {batch_id}')
                return existing
            db.execute(
                'INSERT INTO source_batches (batch_id, source_id, content) VALUES (?, ?, ?)',
                (batch_id, source_id, content),
            )
            append_observations(db, readings)
            return SourceBatch(batch_id, source_id, content)
    except (TypeError, ValueError) as error:
        raise IngestError(str(error)) from error
