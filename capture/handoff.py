from dataclasses import dataclass

from camera.banks import FrameOrigin
from camera.outcomes import WriteState
from camera.ports import RecordingCamera
from capture.attempts import AttemptBook, AttemptState
from capture.claims import TargetClaims
from shift.requests import RegisteredOrder


class FrameProvenanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class HandedOffFrame:
    attempt_id: str
    order: RegisteredOrder
    readings: tuple[object, ...]
    origin: FrameOrigin


class HandoffQueue:
    def __init__(self, attempts: AttemptBook, claims: TargetClaims):
        self._attempts = attempts
        self._claims = claims
        self._frames: dict[str, HandedOffFrame] = {}

    def prepare(self, order_id: str, camera: RecordingCamera) -> HandedOffFrame | None:
        existing = self.get(order_id)
        if existing is not None:
            return existing
        attempt = self._attempts.current(order_id)
        if attempt is None or attempt.state is not AttemptState.FRAME_READY:
            return None
        if attempt.instruction_id is None or attempt.target is None or attempt.attempt_id is None:
            raise FrameProvenanceError(f'incomplete attempt identity for {order_id!r}')
        outcome = camera.outcome(attempt.instruction_id)
        if outcome.state is not WriteState.WRITTEN:
            return None
        live = camera.read_current(attempt.target)
        if (live is None or outcome.origin != live.origin or live.origin.attempt_id != attempt.attempt_id
                or live.origin.instruction_id != attempt.instruction_id
                or live.origin.target != attempt.target):
            raise FrameProvenanceError(f'target frame does not belong to {order_id!r}')
        frame = HandedOffFrame(attempt.attempt_id, attempt.order, tuple(live.readings), live.origin)
        self._attempts.mark_handed_off(order_id, frame.attempt_id)
        self._frames[order_id] = frame
        self._claims.release_frame(attempt.target, attempt.instruction_id)
        return frame

    def get(self, order_id: str) -> HandedOffFrame | None:
        return self._frames.get(order_id)

    def withdraw(self, order_id: str) -> None:
        self._frames.pop(order_id, None)

    def finish(
        self, frame: HandedOffFrame, state: AttemptState, *,
        result_id: str | None = None, reason: str | None = None,
    ) -> None:
        order_id = frame.order.order_id
        if self.get(order_id) is not frame:
            raise ValueError('publication feedback does not own the current handoff')
        self._attempts.record_publication(
            order_id, frame.attempt_id, state, result_id=result_id, reason=reason,
        )
        if state is not AttemptState.RETRY:
            self.withdraw(order_id)
