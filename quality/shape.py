from shift.spec import ShiftSpec


class FrameShapeError(ValueError):
    pass


def require_frame_shape(readings: tuple[object, ...], spec: ShiftSpec) -> None:
    if not readings:
        raise FrameShapeError('empty frame')
    if len(readings) != spec.point_count:
        raise FrameShapeError(f'frame point count does not match revision {spec.revision!r}')
