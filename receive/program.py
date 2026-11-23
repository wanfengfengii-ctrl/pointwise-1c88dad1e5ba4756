from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from daily.calendar import list_day_regions, validate_day
from issue.checks import CheckObservation, observe_check
from issue.package import Adjustment, decode_event
from receive.eligibility import CheckMismatch, EligibilityError


def initialize_programs(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS program_log (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            ref_id TEXT NOT NULL,
            UNIQUE (day, kind, ref_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS postings (
            acceptance_id TEXT PRIMARY KEY NOT NULL REFERENCES receipts(acceptance_id),
            day TEXT NOT NULL,
            position INTEGER NOT NULL,
            events_json TEXT NOT NULL,
            regions_json TEXT NOT NULL,
            wire BLOB NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            superseded_by TEXT
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS postings_by_day ON postings(day, position)')
    db.execute('''
        CREATE TABLE IF NOT EXISTS replacement_links (
            acceptance_id TEXT PRIMARY KEY NOT NULL REFERENCES receipts(acceptance_id),
            target_receipt_number TEXT NOT NULL,
            original_identity TEXT NOT NULL,
            replacement_identity TEXT NOT NULL
        )
    ''')


class ProgramError(EligibilityError):
    pass


def _append_log(db: sqlite3.Connection, day: str, kind: str, ref_id: str) -> int:
    if not db.in_transaction:
        raise RuntimeError('program logging requires a write transaction')
    db.execute('INSERT OR IGNORE INTO program_log (day, kind, ref_id) VALUES (?, ?, ?)', (day, kind, ref_id))
    row = db.execute(
        'SELECT seq FROM program_log WHERE day = ? AND kind = ? AND ref_id = ?', (day, kind, ref_id),
    ).fetchone()
    return int(row[0])


def record_observation_batch(db: sqlite3.Connection, day: str, batch_id: str) -> int:
    return _append_log(db, validate_day(day), 'observation_batch', batch_id)


def record_supplement(db: sqlite3.Connection, day: str, supplement_id: str) -> int:
    return _append_log(db, validate_day(day), 'supplement', supplement_id)


def encode_events(events: Iterable[Adjustment | CheckObservation]) -> str:
    rows = []
    for event in events:
        if isinstance(event, Adjustment):
            rows.append({'op': 'add', 'region': event.region, 'before': event.before, 'after': event.after})
        elif isinstance(event, CheckObservation):
            rows.append({'op': 'check', 'region': event.region, 'name': event.name,
                         'target': event.target, 'matched': event.matched})
        else:
            raise ProgramError('unknown program event type')
    return json.dumps(rows, sort_keys=True, ensure_ascii=False)


def decode_events(text: str) -> tuple[Adjustment | CheckObservation, ...]:
    return tuple(decode_event(row) for row in json.loads(text))


def record_posting(
    db: sqlite3.Connection, acceptance_id: str, day: str,
    events: Iterable[Adjustment | CheckObservation], wire: bytes,
) -> int:
    if not db.in_transaction:
        raise RuntimeError('posting records require a publication transaction')
    position = _append_log(db, validate_day(day), 'posting', acceptance_id)
    regions = sorted({key.region for key in list_day_regions(db, day)})
    db.execute('''
        INSERT INTO postings (acceptance_id, day, position, events_json, regions_json, wire)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (acceptance_id, day, position, encode_events(events), json.dumps(regions), wire))
    return position


def find_posting(db: sqlite3.Connection, acceptance_id: str) -> tuple[str, int, str, bytes, bool] | None:
    row = db.execute('''
        SELECT day, position, events_json, wire, active FROM postings WHERE acceptance_id = ?
    ''', (acceptance_id,)).fetchone()
    return None if row is None else (row[0], int(row[1]), row[2], bytes(row[3]), bool(row[4]))


def find_posting_regions(db: sqlite3.Connection, acceptance_id: str) -> tuple[str, ...] | None:
    row = db.execute('SELECT regions_json FROM postings WHERE acceptance_id = ?', (acceptance_id,)).fetchone()
    return None if row is None else tuple(json.loads(row[0]))


def find_replacement_link(db: sqlite3.Connection, acceptance_id: str) -> tuple[str, str, str] | None:
    row = db.execute('''
        SELECT target_receipt_number, original_identity, replacement_identity
        FROM replacement_links WHERE acceptance_id = ?
    ''', (acceptance_id,)).fetchone()
    return None if row is None else (row[0], row[1], row[2])


def active_postings(db: sqlite3.Connection, day: str) -> tuple[tuple[str, int, tuple, tuple[str, ...]], ...]:
    rows = db.execute('''
        SELECT acceptance_id, position, events_json, regions_json FROM postings
        WHERE day = ? AND active = 1 ORDER BY position, acceptance_id
    ''', (validate_day(day),))
    return tuple((acceptance_id, int(position), decode_events(events), tuple(json.loads(regions)))
                 for acceptance_id, position, events, regions in rows)


def _local_entries(db: sqlite3.Connection, day: str) -> list[tuple[int, str, int]]:
    entries: list[tuple[int, str, int]] = []
    observations = db.execute('''
        SELECT l.seq, o.region, o.litres FROM program_log AS l
        JOIN observations AS o ON o.batch_id = l.ref_id
        WHERE l.kind = 'observation_batch' AND l.day = ? AND o.day = ?
    ''', (day, day))
    entries.extend((int(seq), region, 0 if litres is None else int(litres))
                   for seq, region, litres in observations)
    supplements = db.execute('''
        SELECT l.seq, s.region, s.delta FROM program_log AS l
        JOIN supplements AS s ON s.supplement_id = l.ref_id
        WHERE l.kind = 'supplement' AND l.day = ? AND s.day = ?
    ''', (day, day))
    entries.extend((int(seq), region, int(delta)) for seq, region, delta in supplements)
    entries.sort(key=lambda item: item[0])
    return entries


def day_is_certified(db: sqlite3.Connection, day: str) -> bool:
    """A day has complete recorded evidence only when every local write was journaled."""
    missing_receipt = db.execute('''
        SELECT 1 FROM receipts AS r
        WHERE r.day = ? AND NOT EXISTS (SELECT 1 FROM postings AS p WHERE p.acceptance_id = r.acceptance_id)
        LIMIT 1
    ''', (day,)).fetchone()
    if missing_receipt is not None:
        return False
    missing_supplement = db.execute('''
        SELECT 1 FROM supplements AS s
        WHERE s.day = ? AND NOT EXISTS (
            SELECT 1 FROM program_log AS l
            WHERE l.kind = 'supplement' AND l.day = s.day AND l.ref_id = s.supplement_id
        ) LIMIT 1
    ''', (day,)).fetchone()
    if missing_supplement is not None:
        return False
    missing_batch = db.execute('''
        SELECT 1 FROM observations AS o
        WHERE o.day = ? AND NOT EXISTS (
            SELECT 1 FROM program_log AS l
            WHERE l.kind = 'observation_batch' AND l.day = o.day AND l.ref_id = o.batch_id
        ) LIMIT 1
    ''', (day,)).fetchone()
    return missing_batch is None


@dataclass(frozen=True)
class ReplayOutcome:
    values: dict[str, int]
    deltas: dict[int, dict[str, int]]


def replay_day(
    db: sqlite3.Connection, day: str, *,
    substitute_position: int | None = None,
    substitute_events: Iterable[Adjustment | CheckObservation] | None = None,
) -> ReplayOutcome:
    """Replay every effective program in its original posting position.

    Local observations and supplements enter at the moment they were written, so each
    posting sees only evidence older than its own position.  A substitution replaces
    the events at ``substitute_position`` and every check must keep its recorded truth.
    """
    day = validate_day(day)
    if (substitute_position is None) != (substitute_events is None):
        raise ProgramError('a substitution needs both a position and events')
    registered = {key.region for key in list_day_regions(db, day)}
    timeline = _local_entries(db, day)
    cursor = 0
    running: dict[str, int] = {}
    deltas: dict[int, dict[str, int]] = {}

    def advance_before(limit: int) -> None:
        nonlocal cursor
        while cursor < len(timeline) and timeline[cursor][0] < limit:
            _, region, litres = timeline[cursor]
            if region not in registered:
                raise ProgramError(f'unregistered day region {day} / {region}')
            running[region] = running.get(region, 0) + litres
            cursor += 1

    for acceptance_id, position, events, scope in active_postings(db, day):
        advance_before(position)
        if substitute_position == position:
            events = tuple(substitute_events)
        allowed = set(scope)
        start = dict(running)
        working = dict(start)
        touched: list[str] = []
        for event in events:
            region = event.region
            if region not in allowed:
                raise ProgramError(f'unregistered day region {day} / {region}')
            working.setdefault(region, 0)
            if region not in touched:
                touched.append(region)
            if isinstance(event, Adjustment):
                working[region] += event.after - event.before
                continue
            if substitute_position is not None and position < substitute_position:
                # Programs before the target are fixed history: contribute, never re-verify.
                continue
            local = observe_check(region, event.name, event.target, working[region])
            if local.matched != event.matched:
                raise CheckMismatch(
                    f'check {event.name} in {region} changed from {event.matched} to {local.matched}'
                )
        deltas[position] = {region: working[region] - start.get(region, 0) for region in touched}
        for region, value in working.items():
            running[region] = value

    advance_before(1 << 62)
    return ReplayOutcome(running, deltas)
