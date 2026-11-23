import sqlite3
from dataclasses import dataclass

from archive.storage import encode_ticks
from capture.handoff import HandedOffFrame
from quality.judgment import QualityResult, judge_frame


@dataclass(frozen=True)
class AcceptedFrame:
    handoff: HandedOffFrame
    quality: QualityResult


def prepare_intake(frame: HandedOffFrame) -> AcceptedFrame:
    if frame.origin.attempt_id != frame.attempt_id:
        raise ValueError('handoff origin does not identify the accepted attempt')
    if frame.order.spec.shift_id != frame.order.request.shift_id:
        raise ValueError('handoff specification does not belong to its registered shift')
    return AcceptedFrame(frame, judge_frame(frame.readings, frame.order.spec))


def store_accepted_frame(db: sqlite3.Connection, accepted: AcceptedFrame) -> None:
    frame = accepted.handoff
    registered = db.execute('''
        SELECT o.shift_id, o.segment, s.revision, s.point_count, s.spread_limit
          FROM orders AS o JOIN shifts AS s ON s.shift_id = o.shift_id
         WHERE o.order_id = ?
    ''', (frame.order.order_id,)).fetchone()
    spec = frame.order.spec
    if registered is None or tuple(registered) != (
        spec.shift_id, frame.order.request.segment, spec.revision,
        hex(spec.point_count), hex(spec.spread_limit),
    ):
        raise ValueError('handoff conflicts with the registered order and specification')
    db.execute('''
        INSERT INTO accepted_frames
            (attempt_id, order_id, target, instruction_id, source, readings)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        frame.attempt_id, frame.order.order_id, frame.origin.target,
        frame.origin.instruction_id, frame.origin.source, encode_ticks(accepted.quality.readings),
    ))
