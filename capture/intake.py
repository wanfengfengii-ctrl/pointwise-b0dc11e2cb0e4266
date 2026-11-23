from camera.page_rig import BlockReading, ReadBlockEvent
from camera.ports import RecordingCamera
from capture.attempts import AttemptBook, CaptureAttempt
from capture.handoff import FrameProvenanceError

RECEIVED = 'received'
ALREADY = 'already'
NOT_READY = 'not_ready'


class BlockIntake:
    """Station-side custody of accepted blocks for a dual-block session.

    A block enters custody only through an explicit read window.  The device
    keeps its raw page until the station validates provenance and shape; only
    then does the station tell the device the page may be returned.  Received
    copies stay in station memory so later writes or slot reuse cannot alter a
    signed frame.
    """

    def __init__(self, camera: RecordingCamera, attempts: AttemptBook):
        self._camera = camera
        self._attempts = attempts
        self._received: dict[str, dict[int, BlockReading]] = {}
        self._withdrawn: set[str] = set()

    @property
    def block_size(self) -> int:
        size = self._camera.block_size
        if size is None:
            raise ValueError('block intake requires a dual-block camera')
        return size

    def take(self, event: ReadBlockEvent) -> str:
        attempt = self._attempts.find_by_instruction(event.instruction_id)
        if attempt is None:
            raise KeyError(event.instruction_id)
        if type(event.block) is not int or event.block not in (0, 1):
            raise ValueError('block must be integer 0 or 1')
        blocks = self._received.get(event.instruction_id)
        if blocks is not None and event.block in blocks:
            return ALREADY
        if event.instruction_id in self._withdrawn:
            return NOT_READY
        reading = self._camera.advance(event)
        if reading is None:
            return NOT_READY
        self._validate(reading, event, attempt)
        self._received.setdefault(event.instruction_id, {})[event.block] = reading
        self._camera.return_block(event.instruction_id, event.block)
        return RECEIVED

    def _validate(
        self, reading: BlockReading, event: ReadBlockEvent, attempt: CaptureAttempt
    ) -> None:
        origin = reading.origin
        if (reading.block != event.block
                or origin.instruction_id != event.instruction_id
                or origin.attempt_id != attempt.attempt_id
                or origin.target != attempt.target):
            raise FrameProvenanceError(
                f'block reading does not belong to instruction {event.instruction_id!r}'
            )
        readings = reading.readings
        if (not isinstance(readings, tuple) or len(readings) != self.block_size
                or any(type(tick) is not int for tick in readings)):
            raise FrameProvenanceError(
                f'block reading for {event.instruction_id!r} has the wrong shape'
            )

    def has_both(self, instruction_id: str) -> bool:
        blocks = self._received.get(instruction_id, {})
        return 0 in blocks and 1 in blocks

    def joined(self, instruction_id: str) -> tuple[tuple[int, ...], object]:
        blocks = self._received.get(instruction_id, {})
        if 0 not in blocks or 1 not in blocks:
            raise KeyError(instruction_id)
        first, second = blocks[0], blocks[1]
        if first.origin != second.origin:
            raise FrameProvenanceError('the two blocks do not share one write origin')
        return tuple(first.readings) + tuple(second.readings), first.origin

    def drop(self, instruction_id: str | None) -> None:
        if instruction_id is None:
            return
        self._withdrawn.add(instruction_id)
        self._received.pop(instruction_id, None)

    def is_withdrawn(self, instruction_id: str) -> bool:
        return instruction_id in self._withdrawn
