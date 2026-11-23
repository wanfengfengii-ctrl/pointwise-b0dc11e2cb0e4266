from dataclasses import dataclass, replace
from enum import Enum

from camera.outcomes import WriteOutcome, WriteState
from shift.requests import RegisteredOrder


class AttemptState(str, Enum):
    WAITING = 'waiting'
    IN_FLIGHT = 'in_flight'
    FRAME_READY = 'frame_ready'
    NO_FRAME = 'no_frame'
    HANDED_OFF = 'handed_off'
    RETRY = 'retry'
    REJECTED = 'rejected'
    WITHDRAWN = 'withdrawn'
    PUBLISHED = 'published'


@dataclass(frozen=True)
class CaptureAttempt:
    order: RegisteredOrder
    state: AttemptState = AttemptState.WAITING
    attempt_id: str | None = None
    instruction_id: str | None = None
    target: str | None = None
    outcome: WriteOutcome | None = None
    result_id: str | None = None
    reason: str | None = None


class AttemptBook:
    def __init__(self):
        self._orders: dict[str, CaptureAttempt] = {}
        self._instructions: dict[str, CaptureAttempt] = {}
        self._counts: dict[str, int] = {}

    def enqueue(self, order: RegisteredOrder) -> CaptureAttempt:
        existing = self.current(order.order_id)
        if existing is not None:
            return existing
        attempt = CaptureAttempt(order)
        self._orders[order.order_id] = attempt
        return attempt

    def next_attempt_id(self, order_id: str) -> str:
        return f'{len(order_id)}:{order_id}:{self._counts.get(order_id, 0) + 1}'

    def begin(
        self, order_id: str, attempt_id: str, instruction_id: str, target: str
    ) -> CaptureAttempt:
        pending = self._orders[order_id]
        if pending.state is not AttemptState.WAITING:
            raise ValueError(f'order is not waiting: {order_id!r}')
        if attempt_id != self.next_attempt_id(order_id) or instruction_id in self._instructions:
            raise ValueError('accepted dispatch does not identify the next attempt')
        attempt = CaptureAttempt(
            pending.order, AttemptState.IN_FLIGHT, attempt_id, instruction_id, target,
        )
        self._counts[order_id] = self._counts.get(order_id, 0) + 1
        self._orders[order_id] = attempt
        self._instructions[instruction_id] = attempt
        return attempt

    def current(self, order_id: str) -> CaptureAttempt | None:
        return self._orders.get(order_id)

    def find_by_instruction(self, instruction_id: str) -> CaptureAttempt | None:
        return self._instructions.get(instruction_id)

    def _update(self, attempt: CaptureAttempt) -> CaptureAttempt:
        instruction_id = attempt.instruction_id
        if instruction_id is not None:
            self._instructions[instruction_id] = attempt
        order_id = attempt.order.order_id
        if self._orders[order_id].instruction_id == instruction_id:
            self._orders[order_id] = attempt
        return attempt

    def record_outcome(self, outcome: WriteOutcome) -> CaptureAttempt:
        attempt = self._instructions[outcome.instruction_id]
        if attempt.attempt_id != outcome.attempt_id or attempt.target != outcome.target:
            raise ValueError('device outcome does not match actual attempt')
        state = attempt.state
        if state is AttemptState.IN_FLIGHT:
            if outcome.state is WriteState.WRITTEN:
                state = AttemptState.FRAME_READY
            elif outcome.state is WriteState.NO_WRITE:
                state = AttemptState.NO_FRAME
        observed = self._update(replace(attempt, state=state, outcome=outcome))
        if attempt.state is AttemptState.IN_FLIGHT and state is AttemptState.NO_FRAME:
            self._orders[attempt.order.order_id] = CaptureAttempt(attempt.order)
        return observed

    def mark_handed_off(self, order_id: str, attempt_id: str) -> CaptureAttempt:
        attempt = self._orders[order_id]
        if attempt.attempt_id != attempt_id or attempt.state is not AttemptState.FRAME_READY:
            raise ValueError('handoff requires the current written attempt')
        return self._update(replace(attempt, state=AttemptState.HANDED_OFF))

    def record_publication(
        self, order_id: str, attempt_id: str, state: AttemptState, *,
        result_id: str | None = None, reason: str | None = None,
    ) -> CaptureAttempt:
        attempt = self._orders[order_id]
        if attempt.attempt_id != attempt_id or attempt.state not in (
            AttemptState.HANDED_OFF, AttemptState.RETRY,
        ):
            raise ValueError('publication requires the current eligible handoff')
        if state not in (AttemptState.PUBLISHED, AttemptState.RETRY, AttemptState.REJECTED):
            raise ValueError('unknown publication feedback')
        if (state is AttemptState.PUBLISHED) != (result_id is not None):
            raise ValueError('publication feedback has inconsistent result identity')
        return self._update(replace(attempt, state=state, result_id=result_id, reason=reason))

    def withdraw(self, order_id: str) -> CaptureAttempt:
        attempt = self._orders[order_id]
        if attempt.state in (AttemptState.PUBLISHED, AttemptState.WITHDRAWN):
            return attempt
        return self._update(replace(attempt, state=AttemptState.WITHDRAWN))
