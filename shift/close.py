from dataclasses import dataclass

from shift.registry import ShiftRegistry
from shift.requests import RegisteredOrder
from shift.spec import ShiftSpec


@dataclass(frozen=True)
class ShiftHandoverScope:
    spec: ShiftSpec
    orders: tuple[RegisteredOrder, ...]


def prepare_handover(registry: ShiftRegistry, shift_id: str) -> ShiftHandoverScope | None:
    spec = registry.get_shift(shift_id)
    if spec is None:
        return None
    return ShiftHandoverScope(spec, registry.orders_for_shift(shift_id))
