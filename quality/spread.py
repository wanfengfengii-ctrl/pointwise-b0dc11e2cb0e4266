from dataclasses import dataclass


@dataclass(frozen=True)
class SpreadMeasurement:
    minimum: int
    maximum: int
    spread: int


def measure_spread(ticks: tuple[int, ...]) -> SpreadMeasurement:
    minimum = min(ticks)
    maximum = max(ticks)
    return SpreadMeasurement(minimum, maximum, maximum - minimum)
