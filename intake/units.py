UNITS = ('L', 'mL')


class UnitError(ValueError):
    pass


def to_integer_litres(value, unit, allowed_units):
    if unit not in UNITS or unit not in allowed_units:
        raise UnitError(f'unit is not allowed: {unit!r}')
    if value is None:
        return None
    if type(value) is not int:
        raise UnitError('reading must be an integer or an explicit missing value')
    if unit == 'L':
        return value
    litres, remainder = divmod(value, 1000)
    if remainder:
        raise UnitError(f'{value} mL cannot be expressed as integer litres')
    return litres
