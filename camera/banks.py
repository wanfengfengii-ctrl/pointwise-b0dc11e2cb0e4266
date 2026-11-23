from dataclasses import dataclass


@dataclass(frozen=True)
class FrameOrigin:
    target: str
    instruction_id: str
    attempt_id: str
    source: str


@dataclass(frozen=True)
class BankFrame:
    origin: FrameOrigin
    readings: tuple[object, ...]


class BankSet:
    def __init__(self, targets: tuple[str, ...]):
        targets = tuple(targets)
        if not targets or any(not isinstance(name, str) or not name.strip() for name in targets):
            raise ValueError('each camera target must have a nonblank name')
        if len(set(targets)) != len(targets):
            raise ValueError('camera targets must have distinct names')
        self._frames: dict[str, BankFrame | None] = dict.fromkeys(targets)

    def targets(self) -> tuple[str, ...]:
        return tuple(self._frames)

    def write(self, origin: FrameOrigin, readings: tuple[object, ...]) -> BankFrame:
        if origin.target not in self._frames:
            raise KeyError(origin.target)
        frame = BankFrame(origin, tuple(readings))
        self._frames[origin.target] = frame
        return frame

    def read(self, target: str) -> BankFrame | None:
        return self._frames[target]
