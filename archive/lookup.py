import sqlite3
from dataclasses import dataclass

from archive.storage import ArchiveStore, decode_ticks
from camera.banks import FrameOrigin
from quality.judgment import QualityResult


@dataclass(frozen=True)
class ArchivedCapture:
    result_id: str
    receipt_id: str
    attempt_id: str
    order_id: str
    shift_id: str
    segment: str
    origin: FrameOrigin
    quality: QualityResult


def _decode(row: sqlite3.Row | None) -> ArchivedCapture | None:
    if row is None:
        return None
    return ArchivedCapture(
        row['result_id'], row['receipt_id'], row['attempt_id'], row['order_id'],
        row['shift_id'], row['segment'],
        FrameOrigin(row['target'], row['instruction_id'], row['attempt_id'], row['source']),
        QualityResult(decode_ticks(row['readings']), int(row['spread'], 16), bool(row['needs_review'])),
    )


def get(store: ArchiveStore, result_id: str) -> ArchivedCapture | None:
    with store.reader() as db:
        return _decode(db.execute(
            'SELECT * FROM published_captures WHERE result_id = ?', (result_id,),
        ).fetchone())


def find_by_attempt(db: sqlite3.Connection, attempt_id: str) -> ArchivedCapture | None:
    return _decode(db.execute(
        'SELECT * FROM published_captures WHERE attempt_id = ?', (attempt_id,),
    ).fetchone())


def find_by_order(store: ArchiveStore, order_id: str) -> ArchivedCapture | None:
    with store.reader() as db:
        return _decode(db.execute(
            'SELECT * FROM published_captures WHERE order_id = ?', (order_id,),
        ).fetchone())
