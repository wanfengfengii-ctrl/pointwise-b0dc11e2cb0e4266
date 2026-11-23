from camera.banks import BankFrame, BankSet
from camera.events import DispatchEvent, NoticeEvent, RecordingEvent, WriteEvent
from camera.outcomes import OutcomeLog, WriteOutcome
from camera.writes import AcceptedWrite, WriteExecutor


class RecordingCamera:
    def __init__(self, targets: tuple[str, ...] = ('left', 'right')):
        self._banks = BankSet(targets)
        self._outcomes = OutcomeLog()
        self._executor = WriteExecutor(self._banks, self._outcomes)

    def advance(
        self, event: RecordingEvent, *, attempt_id: str | None = None,
        segment: str | None = None,
    ) -> AcceptedWrite | WriteOutcome:
        if isinstance(event, DispatchEvent):
            if not attempt_id or not segment:
                raise ValueError('dispatch needs an actual attempt and registered segment')
            return self._executor.accept(AcceptedWrite(
                event.instruction_id, event.order_id, segment, attempt_id, event.target,
            ))
        if isinstance(event, WriteEvent):
            return self._executor.execute(event)
        if isinstance(event, NoticeEvent):
            return self._executor.notify(event)
        raise TypeError(f'unsupported recording event: {type(event).__name__}')

    def targets(self) -> tuple[str, ...]:
        return self._banks.targets()

    def read_current(self, target: str) -> BankFrame | None:
        return self._banks.read(target)

    def outcome(self, instruction_id: str) -> WriteOutcome:
        return self._outcomes.get(instruction_id)
