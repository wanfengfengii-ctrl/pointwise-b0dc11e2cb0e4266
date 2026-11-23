from camera.banks import BankFrame, BankSet
from camera.blocks import DualBlockExecutor
from camera.events import DispatchEvent, NoticeEvent, RecordingEvent, WriteEvent
from camera.outcomes import OutcomeLog, WriteOutcome
from camera.page_rig import BlockReading, PageRig, ReadBlockEvent
from camera.writes import AcceptedWrite, WriteExecutor


class RecordingCamera:
    def __init__(
        self, targets: tuple[str, ...] = ('left', 'right'), *,
        pages: PageRig | None = None,
    ):
        self._pages = pages
        if pages is None:
            self._targets = None
            self._banks = BankSet(targets)
            self._outcomes = OutcomeLog()
            self._executor = WriteExecutor(self._banks, self._outcomes)
            self._blocks = None
        else:
            self._targets = tuple(targets)
            self._banks = None
            self._outcomes = None
            self._executor = None
            self._blocks = DualBlockExecutor(pages, self._targets)

    @property
    def block_size(self) -> int | None:
        return None if self._pages is None else self._pages.block_size

    def advance(
        self, event: RecordingEvent | ReadBlockEvent, *, attempt_id: str | None = None,
        segment: str | None = None,
    ) -> AcceptedWrite | WriteOutcome | BlockReading | None:
        if isinstance(event, DispatchEvent):
            if not attempt_id or not segment:
                raise ValueError('dispatch needs an actual attempt and registered segment')
            write = AcceptedWrite(
                event.instruction_id, event.order_id, segment, attempt_id, event.target,
            )
            if self._blocks is not None:
                return self._blocks.accept(write)
            return self._executor.accept(write)
        if isinstance(event, ReadBlockEvent):
            if self._blocks is None:
                raise ValueError('this camera does not provide a block read window')
            return self._blocks.read_block(event)
        if isinstance(event, WriteEvent):
            if self._blocks is not None:
                return self._blocks.execute(event)
            return self._executor.execute(event)
        if isinstance(event, NoticeEvent):
            if self._blocks is not None:
                return self._blocks.notify(event)
            return self._executor.notify(event)
        raise TypeError(f'unsupported recording event: {type(event).__name__}')

    def targets(self) -> tuple[str, ...]:
        if self._blocks is not None:
            return self._targets
        return self._banks.targets()

    def read_current(self, target: str) -> BankFrame | None:
        if self._blocks is not None:
            raise ValueError('a dual-block camera has no single whole frame to read')
        return self._banks.read(target)

    def return_block(self, instruction_id: str, block: int) -> None:
        if self._blocks is not None:
            self._blocks.return_block(instruction_id, block)

    def withdraw(self, instruction_id: str) -> None:
        if self._blocks is not None:
            self._blocks.withdraw(instruction_id)

    def outcome(self, instruction_id: str) -> WriteOutcome:
        if self._blocks is not None:
            return self._blocks.outcome(instruction_id)
        return self._outcomes.get(instruction_id)
