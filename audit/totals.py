from typing import Iterable

from daily.aggregation import RegionBalance


def total_litres(balances: Iterable[RegionBalance]) -> int:
    return sum(balance.litres for balance in balances)
