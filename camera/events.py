from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchEvent:
    order_id: str
    target: str
    instruction_id: str


@dataclass(frozen=True)
class WriteEvent:
    instruction_id: str
    readings: tuple[object, ...] | None
    source: str | None = None


@dataclass(frozen=True)
class NoticeEvent:
    instruction_id: str


RecordingEvent = DispatchEvent | WriteEvent | NoticeEvent
