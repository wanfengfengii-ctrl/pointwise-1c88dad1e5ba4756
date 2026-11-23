from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from daily.calendar import validate_day
from intake.stations import require_identifier
from issue.checks import CheckObservation


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class Adjustment:
    region: str
    before: int
    after: int


@dataclass(frozen=True)
class FrozenPackage:
    source_batch: str
    day: str
    events: tuple[Adjustment | CheckObservation, ...]

    @property
    def identity(self) -> str:
        return package_identity(self)


@dataclass(frozen=True)
class ReplacementPackage:
    """A same-day replacement bound to the ordinary package it replaces."""

    original: FrozenPackage
    replacement: FrozenPackage

    @property
    def original_identity(self) -> str:
        return package_identity(self.original)

    @property
    def identity(self) -> str:
        return replacement_identity(self)


def _require_fields(value: object, fields: set[str], label: str) -> None:
    if not isinstance(value, dict) or value.keys() != fields:
        raise PackageError(f'invalid fields for {label}')


def decode_event(row: object) -> Adjustment | CheckObservation:
    if not isinstance(row, dict):
        raise PackageError('each formal event must be an object')
    verb = row.get('op')
    try:
        if verb == 'add':
            _require_fields(row, {'op', 'region', 'before', 'after'}, 'adjustment')
            region = require_identifier(row['region'], 'region')
            if type(row['before']) is not int or type(row['after']) is not int:
                raise PackageError('adjustment endpoints must be integers')
            return Adjustment(region, row['before'], row['after'])
        if verb == 'check':
            _require_fields(row, {'op', 'region', 'name', 'target', 'matched'}, 'check')
            region = require_identifier(row['region'], 'region')
            name = require_identifier(row['name'], 'check name')
            if type(row['target']) is not int or type(row['matched']) is not bool:
                raise PackageError('checks require an integer target and a boolean observation')
            return CheckObservation(region, name, row['target'], row['matched'])
        raise PackageError(f'unknown formal event {verb!r}')
    except PackageError:
        raise
    except ValueError as error:
        raise PackageError(str(error)) from error


def _event_body(event: Adjustment | CheckObservation) -> dict:
    if isinstance(event, Adjustment):
        return {'op': 'add', **asdict(event)}
    if isinstance(event, CheckObservation):
        return {'op': 'check', **asdict(event)}
    raise PackageError('unknown formal event type')


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _body(package: FrozenPackage) -> dict:
    return {'version': 1, 'source_batch': package.source_batch, 'day': package.day,
            'events': [_event_body(event) for event in package.events]}


def _digest(body: dict) -> str:
    return hashlib.sha256(_canonical(body)).hexdigest()


def package_identity(package: FrozenPackage) -> str:
    return _digest(_body(package))


def encode_package(package: FrozenPackage) -> bytes:
    body = _body(package)
    return _canonical({'identity': _digest(body), 'body': body})


def _replacement_body(package: ReplacementPackage) -> dict:
    original = _body(package.original)
    replacement = _body(package.replacement)
    return {'version': 2, 'kind': 'replacement',
            'original': {'identity': _digest(original), 'body': original},
            'replacement': {'identity': _digest(replacement), 'body': replacement}}


def replacement_identity(package: ReplacementPackage) -> str:
    return _digest(_replacement_body(package))


def encode_replacement(package: ReplacementPackage) -> bytes:
    body = _replacement_body(package)
    return _canonical({'identity': _digest(body), 'body': body})


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageError(f'duplicate exchange key {key}')
        result[key] = value
    return result


def _decode_envelope(wire: bytes) -> dict:
    if not isinstance(wire, bytes):
        raise PackageError('the exchange package must be bytes')
    envelope = json.loads(wire.decode('utf-8'), object_pairs_hook=_unique_keys)
    _require_fields(envelope, {'identity', 'body'}, 'exchange envelope')
    return envelope


def _decode_package_body(body: object) -> FrozenPackage:
    _require_fields(body, {'version', 'source_batch', 'day', 'events'}, 'package body')
    if type(body['version']) is not int or body['version'] != 1:
        raise PackageError('unsupported exchange version')
    source_batch = require_identifier(body['source_batch'], 'source_batch')
    day = validate_day(body['day'])
    if not isinstance(body['events'], list):
        raise PackageError('formal events must be an ordered list')
    events = tuple(decode_event(row) for row in body['events'])
    names = [event.name for event in events if isinstance(event, CheckObservation)]
    if len(set(names)) != len(names):
        raise PackageError('a check name occurs more than once')
    return FrozenPackage(source_batch, day, events)


def decode_package(wire: bytes) -> FrozenPackage:
    try:
        envelope = _decode_envelope(wire)
        body = envelope['body']
        if isinstance(body, dict) and body.get('kind') == 'replacement':
            raise PackageError('a replacement package cannot be accepted as ordinary')
        package = _decode_package_body(body)
        if envelope['identity'] != package.identity:
            raise PackageError('exchange identity does not match the formal content')
        return package
    except PackageError:
        raise
    except (ValueError, TypeError, RecursionError) as error:
        raise PackageError(f'invalid exchange package: {error}') from error


def decode_replacement(wire: bytes) -> ReplacementPackage:
    try:
        envelope = _decode_envelope(wire)
        body = envelope['body']
        _require_fields(body, {'version', 'kind', 'original', 'replacement'}, 'replacement body')
        if type(body['version']) is not int or body['version'] != 2:
            raise PackageError('unsupported replacement version')
        if body['kind'] != 'replacement':
            raise PackageError('not a replacement package')
        parts = []
        for label in ('original', 'replacement'):
            part = body[label]
            _require_fields(part, {'identity', 'body'}, f'{label} package')
            package = _decode_package_body(part['body'])
            if part['identity'] != package.identity:
                raise PackageError(f'{label} identity does not match its content')
            parts.append(package)
        original, replacement = parts
        if original.day != replacement.day:
            raise PackageError('a replacement must belong to the same day as its original')
        package = ReplacementPackage(original, replacement)
        if envelope['identity'] != package.identity:
            raise PackageError('replacement identity does not match the bound content')
        return package
    except PackageError:
        raise
    except (ValueError, TypeError, RecursionError) as error:
        raise PackageError(f'invalid replacement package: {error}') from error
