from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Sequence

from intake.units import UNITS
from receive.transaction import write_transaction


class RegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class Station:
    station_id: str
    region: str
    units: tuple[str, ...]


def require_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{label} must be a nonblank string without NUL')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise ValueError(f'{label} must be valid UTF-8 text') from error
    return value


def initialize_stations(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS stations (
            station_id TEXT PRIMARY KEY NOT NULL,
            region TEXT NOT NULL,
            units TEXT NOT NULL
        )
    ''')


def _station(row: tuple) -> Station:
    station_id, region, units = row
    return Station(station_id, region, tuple(json.loads(units)))


def find_station(db: sqlite3.Connection, station_id: str) -> Station | None:
    row = db.execute(
        'SELECT station_id, region, units FROM stations WHERE station_id = ?',
        (station_id,),
    ).fetchone()
    return None if row is None else _station(row)


def list_stations(db: sqlite3.Connection) -> tuple[Station, ...]:
    rows = db.execute('SELECT station_id, region, units FROM stations ORDER BY station_id')
    return tuple(_station(row) for row in rows)


def register(
    db: sqlite3.Connection, station_id: str, region: str, units: Sequence[str]
) -> Station:
    try:
        require_identifier(station_id, 'station_id')
        require_identifier(region, 'region')
        if not isinstance(units, (list, tuple)) or not units:
            raise ValueError('units must be a nonempty list or tuple')
        if any(unit not in UNITS for unit in units):
            raise ValueError('each allowed unit must be L or mL')
    except ValueError as error:
        raise RegistrationError(str(error)) from error
    station = Station(station_id, region, tuple(sorted(set(units))))
    with write_transaction(db, join=True):
        existing = find_station(db, station_id)
        if existing is None:
            db.execute(
                'INSERT INTO stations (station_id, region, units) VALUES (?, ?, ?)',
                (station.station_id, station.region, json.dumps(station.units)),
            )
            return station
        if existing != station:
            raise RegistrationError(f'conflicting registration for {station_id}')
        return existing
