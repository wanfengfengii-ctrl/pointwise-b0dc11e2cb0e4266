from camera.events import DispatchEvent
from camera.ports import RecordingCamera
from camera.writes import AcceptedWrite
from capture.attempts import AttemptBook, AttemptState, CaptureAttempt
from capture.claims import TargetClaims
from shift.registry import ShiftRegistry
from shift.requests import OrderRequest


class DispatchQueue:
    def __init__(self, registry: ShiftRegistry, attempts: AttemptBook, claims: TargetClaims):
        self.registry = registry
        self._attempts = attempts
        self._claims = claims
        self._pending: set[str] = set()

    def request(self, request: OrderRequest) -> CaptureAttempt:
        order = self.registry.register_order(request)
        attempt = self._attempts.enqueue(order)
        if attempt.state is AttemptState.WAITING:
            self._pending.add(order.order_id)
        return attempt

    def requeue(self, order_id: str) -> None:
        self._pending.add(order_id)

    def remove(self, order_id: str) -> None:
        self._pending.discard(order_id)

    def dispatch(self, event: DispatchEvent, camera: RecordingCamera) -> CaptureAttempt:
        previous = self._attempts.find_by_instruction(event.instruction_id)
        if previous is not None:
            if previous.order.order_id != event.order_id or previous.target != event.target:
                raise ValueError(f'conflicting dispatch for {event.instruction_id!r}')
            return previous
        attempt = self._attempts.current(event.order_id)
        if attempt is None:
            raise KeyError(event.order_id)
        if event.target not in camera.targets():
            raise KeyError(event.target)
        if event.order_id not in self._pending or not self._claims.available(event.target):
            return attempt
        attempt_id = self._attempts.next_attempt_id(event.order_id)
        accepted = camera.advance(event, attempt_id=attempt_id, segment=attempt.order.request.segment)
        expected = AcceptedWrite(
            event.instruction_id, event.order_id, attempt.order.request.segment,
            attempt_id, event.target,
        )
        if accepted != expected:
            raise ValueError('camera returned a different accepted instruction')
        actual = self._attempts.begin(event.order_id, attempt_id, event.instruction_id, event.target)
        self._claims.claim(event.target, event.instruction_id)
        self.remove(event.order_id)
        return actual
