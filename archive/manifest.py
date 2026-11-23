from dataclasses import dataclass

from archive.storage import ArchiveStore
from shift.close import prepare_handover
from shift.registry import ShiftRegistry
from shift.spec import ShiftSpec


@dataclass(frozen=True)
class ManifestEntry:
    order_id: str
    result_id: str
    receipt_id: str
    spread: int
    needs_review: bool


@dataclass(frozen=True)
class ShiftManifest:
    spec: ShiftSpec
    entries: tuple[ManifestEntry, ...]

    @property
    def review_items(self) -> tuple[ManifestEntry, ...]:
        return tuple(entry for entry in self.entries if entry.needs_review)


def list_shift(store: ArchiveStore, registry: ShiftRegistry, shift_id: str) -> ShiftManifest | None:
    scope = prepare_handover(registry, shift_id)
    if scope is None:
        return None
    with store.reader() as db:
        rows = db.execute('''
            SELECT order_id, result_id, receipt_id, spread, needs_review
              FROM published_captures WHERE shift_id = ?
        ''', (scope.spec.shift_id,))
        published = {
            row['order_id']: ManifestEntry(
                row['order_id'], row['result_id'], row['receipt_id'],
                int(row['spread'], 16), bool(row['needs_review']),
            )
            for row in rows
        }
    entries = tuple(published[order.order_id] for order in scope.orders if order.order_id in published)
    return ShiftManifest(scope.spec, entries)
