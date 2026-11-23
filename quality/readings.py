from quality.shape import FrameShapeError


def read_integer_ticks(readings: tuple[object, ...]) -> tuple[int, ...]:
    ticks = []
    for index, reading in enumerate(readings):
        if type(reading) is not int:
            raise FrameShapeError(f'reading at index {index} is not an integer tick')
        ticks.append(reading)
    return tuple(ticks)
