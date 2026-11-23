from dataclasses import dataclass

from camera.banks import BankSet, FrameOrigin
from camera.events import NoticeEvent, WriteEvent
from camera.outcomes import OutcomeLog, WriteOutcome, WriteState


@dataclass(frozen=True)
class AcceptedWrite:
    instruction_id: str
    order_id: str
    segment: str
    attempt_id: str
    target: str


class WriteExecutor:
    def __init__(self, banks: BankSet, outcomes: OutcomeLog):
        self._banks = banks
        self._outcomes = outcomes
        self._accepted: dict[str, AcceptedWrite] = {}

    def accept(self, write: AcceptedWrite) -> AcceptedWrite:
        if not isinstance(write.instruction_id, str) or not write.instruction_id.strip():
            raise ValueError('instruction_id must be a nonblank string')
        existing = self._accepted.get(write.instruction_id)
        if existing is not None:
            if existing != write:
                raise ValueError(f'conflicting dispatch for {write.instruction_id!r}')
            return existing
        if write.target not in self._banks.targets():
            raise KeyError(write.target)
        self._outcomes.register(write.instruction_id, write.target, write.attempt_id)
        self._accepted[write.instruction_id] = write
        return write

    def execute(self, event: WriteEvent) -> WriteOutcome:
        write = self._accepted[event.instruction_id]
        outcome = self._outcomes.get(write.instruction_id)
        state = WriteState.NO_WRITE if event.readings is None else WriteState.WRITTEN
        if outcome.state is not WriteState.IN_FLIGHT:
            if outcome.state is not state:
                raise ValueError(f'conflicting terminal state for {write.instruction_id!r}')
            return outcome
        if event.readings is None:
            return self._outcomes.settle(write.instruction_id, state)
        if not isinstance(event.source, str) or not event.source.strip():
            raise ValueError('a physical write needs a nonblank recording source')
        origin = FrameOrigin(write.target, write.instruction_id, write.attempt_id, event.source)
        self._banks.write(origin, event.readings)
        return self._outcomes.settle(write.instruction_id, state, origin)

    def notify(self, event: NoticeEvent) -> WriteOutcome:
        return self._outcomes.record_notice(event.instruction_id)
