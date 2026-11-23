from dataclasses import dataclass

from quality.readings import read_integer_ticks
from quality.shape import require_frame_shape
from quality.spread import measure_spread
from shift.spec import ShiftSpec


@dataclass(frozen=True)
class QualityResult:
    readings: tuple[int, ...]
    spread: int
    needs_review: bool


def judge_frame(readings: tuple[object, ...], spec: ShiftSpec) -> QualityResult:
    require_frame_shape(readings, spec)
    ticks = read_integer_ticks(readings)
    spread = measure_spread(ticks).spread
    return QualityResult(ticks, spread, spread > spec.spread_limit)
