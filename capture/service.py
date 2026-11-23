from os import PathLike

from archive.issue import PublicationError, publish
from archive.lookup import ArchivedCapture, find_by_order, get
from archive.manifest import ShiftManifest, list_shift
from archive.storage import ArchiveStore
from camera.events import DispatchEvent, RecordingEvent
from camera.outcomes import WriteOutcome
from camera.page_rig import ReadBlockEvent
from camera.ports import RecordingCamera
from capture.attempts import AttemptBook, AttemptState, CaptureAttempt
from capture.cancel import cancel_order
from capture.claims import TargetClaims
from capture.dispatch import DispatchQueue
from capture.handoff import HandedOffFrame
from capture.handoff import HandoffQueue
from capture.intake import BlockIntake
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
        self.intake = (
            BlockIntake(camera, self.attempts)
            if camera is not None and camera.block_size is not None else None
        )

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

    def _dual(self) -> bool:
        return self.intake is not None

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

    def advance(self, event: RecordingEvent | ReadBlockEvent) -> CaptureAttempt | WriteOutcome | str:
        camera = self._camera()
        if isinstance(event, ReadBlockEvent):
            if self.intake is None:
                raise ValueError('this session does not provide a block read window')
            return self.intake.take(event)
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
        if observed.state is AttemptState.FRAME_READY and outcome.noticed and not self._dual():
            self.handoffs.prepare(observed.order.order_id, camera)
        return outcome

    def cancel(self, order_id: str) -> CaptureAttempt | ArchivedCapture:
        attempt = self.attempts.current(order_id)
        instruction_id = attempt.instruction_id if attempt is not None else None
        withdrawn = cancel_order(
            order_id, self.store, self.attempts, self.claims, self._dispatch, self.handoffs,
        )
        if (self._dual() and isinstance(withdrawn, CaptureAttempt)
                and instruction_id is not None):
            self.intake.drop(instruction_id)
            self._camera().withdraw(instruction_id)
        return withdrawn

    def publish(self, order_id: str) -> ArchivedCapture:
        archived = find_by_order(self.store, order_id)
        if archived is not None:
            return archived
        if self.registry.get_order(order_id) is None:
            raise KeyError(order_id)
        attempt = self.attempts.current(order_id)
        if attempt is not None and attempt.state is AttemptState.REJECTED:
            raise FrameShapeError(attempt.reason or 'frame rejected')
        if self._dual():
            return self._publish_split(order_id, attempt)
        frame = self.handoffs.prepare(order_id, self._camera())
        if frame is None:
            state = attempt.state.value if attempt is not None else 'unavailable'
            raise CaptureNotReady(f'{order_id!r} has no eligible handoff ({state})')
        return self._issue(order_id, frame)

    def _publish_split(self, order_id: str, attempt: CaptureAttempt | None) -> ArchivedCapture:
        if attempt is None or attempt.instruction_id is None:
            raise CaptureNotReady(f'{order_id!r} has no received blocks')
        if self.intake.is_withdrawn(attempt.instruction_id):
            raise CaptureNotReady(f'{order_id!r} was withdrawn before both blocks arrived')
        if not self.intake.has_both(attempt.instruction_id):
            raise CaptureNotReady(f'{order_id!r} is missing a received block')
        readings, origin = self.intake.joined(attempt.instruction_id)
        if attempt.state not in (AttemptState.FRAME_READY, AttemptState.RETRY):
            raise CaptureNotReady(f'{order_id!r} is not ready to issue')
        frame = HandedOffFrame(attempt.attempt_id, attempt.order, readings, origin)
        return self._issue(order_id, frame)

    def _issue(self, order_id: str, frame: HandedOffFrame) -> ArchivedCapture:
        try:
            archived = _publish_frame(self.store, frame)
        except FrameShapeError as exc:
            self._reject(frame, str(exc))
            raise
        except PublicationError:
            self._retry(frame)
            raise
        if archived.attempt_id != frame.attempt_id or archived.order_id != order_id:
            raise ValueError('archive feedback does not identify the published attempt')
        self._complete(frame, archived.result_id)
        return archived

    def _reject(self, frame: HandedOffFrame, reason: str) -> None:
        if self._dual():
            self.attempts.record_split_publication(
                frame.order.order_id, frame.attempt_id, AttemptState.REJECTED, reason=reason,
            )
        else:
            self.handoffs.finish(frame, AttemptState.REJECTED, reason=reason)

    def _retry(self, frame: HandedOffFrame) -> None:
        if self._dual():
            self.attempts.record_split_publication(
                frame.order.order_id, frame.attempt_id, AttemptState.RETRY,
            )
        else:
            self.handoffs.finish(frame, AttemptState.RETRY)

    def _complete(self, frame: HandedOffFrame, result_id: str) -> None:
        if self._dual():
            self.attempts.record_split_publication(
                frame.order.order_id, frame.attempt_id, AttemptState.PUBLISHED,
                result_id=result_id,
            )
        else:
            self.handoffs.finish(frame, AttemptState.PUBLISHED, result_id=result_id)

    def get(self, result_id: str) -> ArchivedCapture | None:
        return _get_capture(self.store, result_id)

    def list(self, shift_id: str) -> ShiftManifest | None:
        return list_shift(self.store, self.registry, shift_id)
