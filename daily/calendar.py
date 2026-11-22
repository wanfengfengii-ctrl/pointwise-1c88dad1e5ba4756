from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from intake.stations import list_stations, require_identifier


@dataclass(frozen=True, order=True)
class DayRegion:
    day: str
    region: str


def validate_day(value: object) -> str:
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            if parsed.isoformat() == value:
                return value
    raise ValueError('day must be a valid YYYY-MM-DD date')


def list_day_regions(db: sqlite3.Connection, day: str) -> tuple[DayRegion, ...]:
    day = validate_day(day)
    return tuple(DayRegion(day, region) for region in sorted({item.region for item in list_stations(db)}))


def require_day_region(db: sqlite3.Connection, day: str, region: str) -> DayRegion:
    key = DayRegion(validate_day(day), require_identifier(region, 'region'))
    if key not in list_day_regions(db, key.day):
        raise ValueError(f'unregistered day region {key.day} / {key.region}')
    return key
