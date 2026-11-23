from dataclasses import dataclass, replace
from enum import Enum

from camera.banks import FrameOrigin


class WriteState(str, Enum):
    IN_FLIGHT = 'in_flight'
    WRITTEN = 'written'
    NO_WRITE = 'no_write'


@dataclass(frozen=True)
class WriteOutcome:
    instruction_id: str
    target: str
    attempt_id: str
    state: WriteState = WriteState.IN_FLIGHT
    origin: FrameOrigin | None = None
    noticed: bool = False


class OutcomeLog:
    def __init__(self):
        self._outcomes: dict[str, WriteOutcome] = {}

    def register(self, instruction_id: str, target: str, attempt_id: str) -> WriteOutcome:
        if instruction_id in self._outcomes:
            raise ValueError(f'instruction already registered: {instruction_id!r}')
        outcome = WriteOutcome(instruction_id, target, attempt_id)
        self._outcomes[instruction_id] = outcome
        return outcome

    def settle(
        self, instruction_id: str, state: WriteState, origin: FrameOrigin | None = None
    ) -> WriteOutcome:
        outcome = self.get(instruction_id)
        if state is WriteState.IN_FLIGHT or (state is WriteState.WRITTEN) != (origin is not None):
            raise ValueError('settlement requires a matching terminal outcome')
        if origin is not None and (
            origin.instruction_id != instruction_id or origin.target != outcome.target
            or origin.attempt_id != outcome.attempt_id
        ):
            raise ValueError('write origin does not match accepted instruction')
        if outcome.state is not WriteState.IN_FLIGHT:
            if outcome.state is not state or outcome.origin != origin:
                raise ValueError(f'conflicting terminal state for {instruction_id!r}')
            return outcome
        settled = replace(outcome, state=state, origin=origin)
        self._outcomes[instruction_id] = settled
        return settled

    def record_notice(self, instruction_id: str) -> WriteOutcome:
        outcome = self.get(instruction_id)
        if not outcome.noticed:
            outcome = replace(outcome, noticed=True)
            self._outcomes[instruction_id] = outcome
        return outcome

    def get(self, instruction_id: str) -> WriteOutcome:
        return self._outcomes[instruction_id]
