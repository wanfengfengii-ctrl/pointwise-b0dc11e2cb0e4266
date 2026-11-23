from archive.lookup import ArchivedCapture, find_by_order
from archive.storage import ArchiveStore
from capture.attempts import AttemptBook, CaptureAttempt
from capture.claims import TargetClaims
from capture.dispatch import DispatchQueue
from capture.handoff import HandoffQueue


def cancel_order(
    order_id: str, store: ArchiveStore, attempts: AttemptBook, claims: TargetClaims,
    dispatch: DispatchQueue, handoffs: HandoffQueue,
) -> CaptureAttempt | ArchivedCapture:
    archived = find_by_order(store, order_id)
    if archived is not None:
        return archived
    attempt = attempts.current(order_id)
    if attempt is None:
        order = dispatch.registry.get_order(order_id)
        if order is None:
            raise KeyError(order_id)
        attempt = attempts.enqueue(order)
    withdrawn = attempts.withdraw(order_id)
    dispatch.remove(order_id)
    handoffs.withdraw(order_id)
    if attempt.target is not None and attempt.instruction_id is not None:
        claims.withdraw(attempt.target, attempt.instruction_id)
    return withdrawn
