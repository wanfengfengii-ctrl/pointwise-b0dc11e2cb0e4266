from os import PathLike

from archive.issue import PublicationError, publish
from archive.lookup import ArchivedCapture, find_by_order, get
from archive.manifest import ShiftManifest, list_shift
from archive.storage import ArchiveStore
from camera.events import DispatchEvent, RecordingEvent
from camera.outcomes import WriteOutcome
from camera.ports import RecordingCamera
from capture.attempts import AttemptBook, AttemptState, CaptureAttempt
from capture.cancel import cancel_order
from capture.claims import TargetClaims
from capture.dispatch import DispatchQueue
from capture.handoff import HandoffQueue
from quality.shape import FrameShapeError
from shift.registry import ShiftRegistry
from shift.requests import OrderRequest
from shift.spec import ShiftSpec


class CaptureNotReady(RuntimeError):
    pass


_publish_frame = publish
_get_capture = get


class ShiftCaptureSession:
    def __init__(self, database: str | PathLike[str], camera: RecordingCamera | None = None):
        self.camera = camera
        self.store = ArchiveStore(database)
        self.registry = ShiftRegistry(self.store.transaction, self.store.reader)
        try:
            self.registry.initialize()
            self.store.initialize()
        except BaseException:
            self.store.close()
            raise
        self.attempts = AttemptBook()
        self.claims = TargetClaims()
        self.handoffs = HandoffQueue(self.attempts, self.claims)
        self._dispatch = DispatchQueue(self.registry, self.attempts, self.claims)

    def __enter__(self) -> 'ShiftCaptureSession':
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.store.close()

    def _camera(self) -> RecordingCamera:
        if self.camera is None:
            raise CaptureNotReady('this archive session has no recording camera')
        return self.camera

    def open(self, spec: ShiftSpec) -> ShiftSpec:
        return self.registry.open(spec)

    def request(self, request: OrderRequest) -> CaptureAttempt | ArchivedCapture:
        request.validate()
        archived = find_by_order(self.store, request.order_id)
        if archived is not None:
            self.registry.register_order(request)
            return archived
        self._camera()
        return self._dispatch.request(request)

    def advance(self, event: RecordingEvent) -> CaptureAttempt | WriteOutcome:
        camera = self._camera()
        if isinstance(event, DispatchEvent):
            return self._dispatch.dispatch(event, camera)
        camera.advance(event)
        outcome = camera.outcome(event.instruction_id)
        previous = self.attempts.find_by_instruction(outcome.instruction_id)
        if previous is None:
            return outcome
        observed = self.attempts.record_outcome(outcome)
        self.claims.observe_outcome(outcome)
        if previous.state is AttemptState.IN_FLIGHT and observed.state is AttemptState.NO_FRAME:
            self._dispatch.requeue(observed.order.order_id)
        if observed.state is AttemptState.FRAME_READY and outcome.noticed:
            self.handoffs.prepare(observed.order.order_id, camera)
        return outcome

    def cancel(self, order_id: str) -> CaptureAttempt | ArchivedCapture:
        return cancel_order(
            order_id, self.store, self.attempts, self.claims, self._dispatch, self.handoffs,
        )

    def publish(self, order_id: str) -> ArchivedCapture:
        archived = find_by_order(self.store, order_id)
        if archived is not None:
            return archived
        if self.registry.get_order(order_id) is None:
            raise KeyError(order_id)
        attempt = self.attempts.current(order_id)
        if attempt is not None and attempt.state is AttemptState.REJECTED:
            raise FrameShapeError(attempt.reason or 'frame rejected')
        frame = self.handoffs.prepare(order_id, self._camera())
        if frame is None:
            state = attempt.state.value if attempt is not None else 'unavailable'
            raise CaptureNotReady(f'{order_id!r} has no eligible handoff ({state})')
        try:
            archived = _publish_frame(self.store, frame)
        except FrameShapeError as exc:
            self.handoffs.finish(frame, AttemptState.REJECTED, reason=str(exc))
            raise
        except PublicationError:
            self.handoffs.finish(frame, AttemptState.RETRY)
            raise
        if archived.attempt_id != frame.attempt_id or archived.order_id != order_id:
            raise ValueError('archive feedback does not identify the published attempt')
        self.handoffs.finish(frame, AttemptState.PUBLISHED, result_id=archived.result_id)
        return archived

    def get(self, result_id: str) -> ArchivedCapture | None:
        return _get_capture(self.store, result_id)

    def list(self, shift_id: str) -> ShiftManifest | None:
        return list_shift(self.store, self.registry, shift_id)
