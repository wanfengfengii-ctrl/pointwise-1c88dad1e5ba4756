from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping

from daily.calendar import validate_day
from intake.stations import find_station, require_identifier
from intake.units import to_integer_litres


@dataclass(frozen=True)
class Observation:
    batch_id: str
    source_id: str
    reading_id: str
    station_id: str
    day: str
    region: str
    unit: str
    raw: int | None
    litres: int | None


def initialize_records(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS observations (
            batch_id TEXT NOT NULL REFERENCES source_batches(batch_id),
            ordinal INTEGER NOT NULL,
            reading_id TEXT NOT NULL,
            station_id TEXT NOT NULL REFERENCES stations(station_id),
            day TEXT NOT NULL,
            region TEXT NOT NULL,
            unit TEXT NOT NULL,
            raw TEXT,
            litres TEXT,
            PRIMARY KEY (batch_id, reading_id),
            UNIQUE (batch_id, ordinal)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS observations_by_day ON observations(day)')


def normalize_observation(
    db: sqlite3.Connection, batch_id: str, source_id: str, row: Mapping[str, object]
) -> Observation:
    if not isinstance(row, Mapping) or row.keys() != {'reading_id', 'station_id', 'day', 'unit', 'value'}:
        raise ValueError('an observation needs reading_id, station_id, day, unit and value')
    reading_id = require_identifier(row['reading_id'], 'reading_id')
    station_id = require_identifier(row['station_id'], 'station_id')
    day = validate_day(row['day'])
    station = find_station(db, station_id)
    if station is None:
        raise ValueError(f'unknown station {station_id}')
    unit, raw = row['unit'], row['value']
    litres = to_integer_litres(raw, unit, station.units)
    return Observation(batch_id, source_id, reading_id, station_id, day, station.region, unit, raw, litres)


def _decimal_or_null(value: int | None) -> str | None:
    return None if value is None else str(value)


def append_observations(db: sqlite3.Connection, readings: Iterable[Observation]) -> None:
    db.executemany('''
        INSERT INTO observations
            (batch_id, ordinal, reading_id, station_id, day, region, unit, raw, litres)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        (item.batch_id, ordinal, item.reading_id, item.station_id, item.day, item.region,
         item.unit, _decimal_or_null(item.raw), _decimal_or_null(item.litres))
        for ordinal, item in enumerate(readings)
    ))


def read_day_observations(db: sqlite3.Connection, day: str) -> tuple[Observation, ...]:
    rows = db.execute('''
        SELECT o.batch_id, b.source_id, o.reading_id, o.station_id, o.day, o.region,
               o.unit, o.raw, o.litres
        FROM observations AS o JOIN source_batches AS b ON b.batch_id = o.batch_id
        WHERE o.day = ? ORDER BY o.batch_id, o.ordinal
    ''', (day,))
    return tuple(
        Observation(*fields, None if raw is None else int(raw), None if litres is None else int(litres))
        for *fields, raw, litres in rows
    )
