from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Iterable, Mapping

from daily.calendar import validate_day
from daily.snapshots import take_day_snapshot
from intake.stations import require_identifier
from issue.checks import observe_check
from issue.trial import TrialSegment, finish_trial, open_trial
from receive.transaction import write_transaction


class CorrectionError(ValueError):
    pass


@dataclass
class WorkBatch:
    batch_id: str
    day: str
    base_values: dict[str, int]
    values: dict[str, int]
    formal_events: list[dict[str, object]] = field(default_factory=list)
    trial: TrialSegment | None = None
    sealed: bool = False


def initialize_work_batches(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS work_batches (
            batch_id TEXT PRIMARY KEY NOT NULL,
            day TEXT NOT NULL,
            state TEXT NOT NULL,
            sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1))
        )
    ''')


def find_work_batch(db: sqlite3.Connection, batch_id: str) -> WorkBatch | None:
    row = db.execute(
        'SELECT day, state, sealed FROM work_batches WHERE batch_id = ?', (batch_id,),
    ).fetchone()
    if row is None:
        return None
    day, encoded, sealed = row
    state = json.loads(encoded)
    trial = None if state['trial'] is None else TrialSegment(**state['trial'])
    return WorkBatch(batch_id, day, state['base_values'], state['values'], state['formal_events'], trial, bool(sealed))


def _save_work_batch(db: sqlite3.Connection, batch: WorkBatch) -> None:
    state = {
        'base_values': batch.base_values,
        'values': batch.values,
        'formal_events': batch.formal_events,
        'trial': None if batch.trial is None else asdict(batch.trial),
    }
    db.execute('''
        INSERT INTO work_batches (batch_id, day, state) VALUES (?, ?, ?)
        ON CONFLICT (batch_id) DO UPDATE SET state = excluded.state
    ''', (batch.batch_id, batch.day, json.dumps(state, sort_keys=True)))


def _begin_work_batch(db: sqlite3.Connection, batch_id: str, day: str) -> WorkBatch:
    snapshot = take_day_snapshot(db, day)
    if not snapshot.established or not snapshot.registered:
        raise CorrectionError(f'no source day ledger for {day}')
    base_values = snapshot.values()
    return WorkBatch(batch_id, day, base_values, base_values.copy())


def _perform(batch: WorkBatch, operation: Mapping[str, object]) -> None:
    if not isinstance(operation, Mapping):
        raise CorrectionError('an operation must be a mapping')
    verb = operation.get('op')
    if verb in ('begin_trial', 'keep_trial', 'withdraw_trial'):
        if operation.keys() != {'op'}:
            raise CorrectionError('trial commands take only op')
        if verb == 'begin_trial':
            batch.trial = open_trial(batch.values, batch.trial)
        else:
            finish_trial(batch.trial, batch.values, batch.formal_events, keep=verb == 'keep_trial')
            batch.trial = None
        return

    if verb == 'add':
        required = {'op', 'region', 'litres'}
    elif verb == 'check':
        required = {'op', 'region', 'name', 'target'}
    else:
        raise CorrectionError(f'unknown operation {verb!r}')
    if operation.keys() != required:
        raise CorrectionError(f'invalid fields for {verb}')
    region = require_identifier(operation['region'], 'region')
    if region not in batch.base_values:
        raise CorrectionError(f'unregistered day region {batch.day} / {region}')
    values = batch.values if batch.trial is None else batch.trial.values
    events = batch.formal_events if batch.trial is None else batch.trial.events
    if verb == 'add':
        delta = operation['litres']
        if type(delta) is not int:
            raise CorrectionError('correction must use integer litres')
        before = values[region]
        values[region] = before + delta
        events.append({'op': 'add', 'region': region, 'before': before, 'after': values[region]})
        return

    observed = observe_check(region, operation['name'], operation['target'], values[region])
    if any(event.get('name') == observed.name for event in batch.formal_events):
        raise CorrectionError(f'duplicate check name {observed.name}')
    if batch.trial is not None and any(event.get('name') == observed.name for event in events):
        raise CorrectionError(f'duplicate check name {observed.name}')
    events.append({'op': 'check', **asdict(observed)})


def correct(
    db: sqlite3.Connection, batch_id: str, day: str, operations: Iterable[Mapping[str, object]],
) -> WorkBatch:
    try:
        require_identifier(batch_id, 'batch_id')
        day = validate_day(day)
        with write_transaction(db):
            batch = find_work_batch(db, batch_id)
            if batch is None:
                batch = _begin_work_batch(db, batch_id, day)
            elif batch.day != day or batch.sealed:
                raise CorrectionError(f'work batch {batch_id} is sealed or belongs to another date')
            for operation in operations:
                _perform(batch, operation)
            _save_work_batch(db, batch)
            return batch
    except CorrectionError:
        raise
    except (TypeError, ValueError) as error:
        raise CorrectionError(str(error)) from error


def seal_work_batch(db: sqlite3.Connection, batch_id: str) -> None:
    with write_transaction(db, join=True):
        batch = find_work_batch(db, batch_id)
        if batch is None or batch.trial is not None:
            raise CorrectionError(f'work batch {batch_id} is missing or has an open trial')
        db.execute('UPDATE work_batches SET sealed = 1 WHERE batch_id = ?', (batch_id,))
