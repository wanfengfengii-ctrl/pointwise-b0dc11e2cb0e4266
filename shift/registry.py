import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from shift.requests import OrderRequest, RegisteredOrder, same_request
from shift.spec import ShiftSpec, validate_spec


ConnectionContext = Callable[[], AbstractContextManager[sqlite3.Connection]]

_ORDER_ROWS = '''
    SELECT o.registration_no, o.order_id, o.segment, s.shift_id,
           s.revision, s.point_count, s.spread_limit
      FROM orders AS o JOIN shifts AS s ON s.shift_id = o.shift_id
'''


def _spec(row: sqlite3.Row) -> ShiftSpec:
    return ShiftSpec(
        row['shift_id'], row['revision'], int(row['point_count'], 16),
        int(row['spread_limit'], 16),
    )


def _order(row: sqlite3.Row) -> RegisteredOrder:
    request = OrderRequest(row['shift_id'], row['order_id'], row['segment'])
    return RegisteredOrder(request, _spec(row), row['registration_no'])


class ShiftRegistry:
    def __init__(self, transaction: ConnectionContext, reader: ConnectionContext):
        self._transaction = transaction
        self._reader = reader

    def initialize(self) -> None:
        with self._transaction() as db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS shifts (
                    shift_id TEXT PRIMARY KEY NOT NULL,
                    revision TEXT NOT NULL,
                    point_count TEXT NOT NULL,
                    spread_limit TEXT NOT NULL
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    registration_no INTEGER PRIMARY KEY,
                    order_id TEXT NOT NULL UNIQUE,
                    shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
                    segment TEXT NOT NULL
                )
            ''')
            db.execute('''
                CREATE INDEX IF NOT EXISTS orders_by_shift
                    ON orders (shift_id, registration_no)
            ''')

    def open(self, spec: ShiftSpec) -> ShiftSpec:
        validate_spec(spec)
        with self._transaction() as db:
            existing = self._get_shift(db, spec.shift_id)
            if existing is not None:
                if existing != spec:
                    raise ValueError(f'conflicting specification for {spec.shift_id!r}')
                return existing
            db.execute(
                'INSERT INTO shifts VALUES (?, ?, ?, ?)',
                (spec.shift_id, spec.revision, hex(spec.point_count), hex(spec.spread_limit)),
            )
        return spec

    def register_order(self, request: OrderRequest) -> RegisteredOrder:
        request.validate()
        with self._transaction() as db:
            existing = self._get_order(db, request.order_id)
            if existing is not None:
                if not same_request(existing, request):
                    raise ValueError(f'conflicting request for {request.order_id!r}')
                return existing
            spec = self._get_shift(db, request.shift_id)
            if spec is None:
                raise KeyError(request.shift_id)
            cursor = db.execute(
                'INSERT INTO orders (order_id, shift_id, segment) VALUES (?, ?, ?)',
                (request.order_id, request.shift_id, request.segment),
            )
            return RegisteredOrder(request, spec, cursor.lastrowid)

    @staticmethod
    def _get_shift(db: sqlite3.Connection, shift_id: str) -> ShiftSpec | None:
        row = db.execute('SELECT * FROM shifts WHERE shift_id = ?', (shift_id,)).fetchone()
        return _spec(row) if row is not None else None

    @staticmethod
    def _get_order(db: sqlite3.Connection, order_id: str) -> RegisteredOrder | None:
        row = db.execute(_ORDER_ROWS + ' WHERE o.order_id = ?', (order_id,)).fetchone()
        return _order(row) if row is not None else None

    def get_shift(self, shift_id: str) -> ShiftSpec | None:
        with self._reader() as db:
            return self._get_shift(db, shift_id)

    def get_order(self, order_id: str) -> RegisteredOrder | None:
        with self._reader() as db:
            return self._get_order(db, order_id)

    def orders_for_shift(self, shift_id: str) -> tuple[RegisteredOrder, ...]:
        with self._reader() as db:
            rows = db.execute(
                _ORDER_ROWS + ' WHERE o.shift_id = ? ORDER BY o.registration_no',
                (shift_id,),
            )
            return tuple(_order(row) for row in rows)
