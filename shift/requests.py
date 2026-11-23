from dataclasses import dataclass

from shift.spec import ShiftSpec


@dataclass(frozen=True)
class OrderRequest:
    shift_id: str
    order_id: str
    segment: str

    def validate(self) -> None:
        for name, value in (
            ('shift_id', self.shift_id),
            ('order_id', self.order_id),
            ('segment', self.segment),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be a nonblank string')


@dataclass(frozen=True)
class RegisteredOrder:
    request: OrderRequest
    spec: ShiftSpec
    registration_no: int

    @property
    def order_id(self) -> str:
        return self.request.order_id


def same_request(order: RegisteredOrder, request: OrderRequest) -> bool:
    return order.request == request
