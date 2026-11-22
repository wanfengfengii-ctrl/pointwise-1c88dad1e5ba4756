from __future__ import annotations

import json
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from intake.records import Observation


def canonical_batch_content(source_id: str, observations: Iterable[Observation]) -> str:
    rows = [
        {'reading_id': row.reading_id, 'station_id': row.station_id, 'day': row.day,
         'unit': row.unit, 'value': row.raw}
        for row in observations
    ]
    return json.dumps({'source_id': source_id, 'rows': rows}, sort_keys=True, separators=(',', ':'))
