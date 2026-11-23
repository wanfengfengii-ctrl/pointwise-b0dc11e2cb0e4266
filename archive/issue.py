import sqlite3

from archive.intake import prepare_intake, store_accepted_frame
from archive.lookup import ArchivedCapture, find_by_attempt
from archive.storage import ArchiveStore
from capture.handoff import HandedOffFrame


class PublicationError(RuntimeError):
    pass


def publish(store: ArchiveStore, frame: HandedOffFrame) -> ArchivedCapture:
    accepted = prepare_intake(frame)
    try:
        with store.transaction() as db:
            existing = find_by_attempt(db, frame.attempt_id)
            if existing is not None:
                if (existing.order_id != frame.order.order_id or existing.origin != frame.origin
                        or existing.shift_id != frame.order.spec.shift_id
                        or existing.segment != frame.order.request.segment
                        or existing.quality != accepted.quality):
                    raise ValueError('conflicting replay for an already published attempt')
                return existing
            result_id = f'capture/{frame.attempt_id}'
            receipt_id = f'receipt/{result_id}'
            store_accepted_frame(db, accepted)
            db.execute('INSERT INTO captures VALUES (?, ?, ?, ?)', (
                result_id, frame.attempt_id, hex(accepted.quality.spread),
                int(accepted.quality.needs_review),
            ))
            db.execute('INSERT INTO receipts VALUES (?, ?)', (result_id, receipt_id))
            archived = find_by_attempt(db, frame.attempt_id)
            if archived is None:
                raise PublicationError('incomplete archive association')
            return archived
    except sqlite3.Error as exc:
        raise PublicationError(f'archive publication failed for {frame.order.order_id!r}') from exc
