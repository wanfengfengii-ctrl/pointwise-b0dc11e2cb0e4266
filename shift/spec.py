from dataclasses import dataclass


@dataclass(frozen=True)
class ShiftSpec:
    shift_id: str
    revision: str
    point_count: int
    spread_limit: int


def validate_spec(spec: ShiftSpec) -> ShiftSpec:
    if not isinstance(spec.shift_id, str) or not spec.shift_id.strip():
        raise ValueError('shift_id must be a nonblank string')
    if not isinstance(spec.revision, str) or not spec.revision.strip():
        raise ValueError('revision must be a nonblank string')
    if type(spec.point_count) is not int or spec.point_count <= 0:
        raise ValueError('point_count must be a positive integer')
    if type(spec.spread_limit) is not int or spec.spread_limit < 0:
        raise ValueError('spread_limit must be a nonnegative integer')
    return spec
