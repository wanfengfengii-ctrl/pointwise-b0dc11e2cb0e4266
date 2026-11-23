import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from os import PathLike, fspath
from pathlib import Path


def encode_ticks(readings: tuple[int, ...]) -> str:
    return json.dumps([hex(tick) for tick in readings], separators=(',', ':'))


def decode_ticks(encoded: str) -> tuple[int, ...]:
    return tuple(int(tick, 16) for tick in json.loads(encoded))


class ArchiveStore:
    def __init__(self, database: str | PathLike[str]):
        if fspath(database) in ('', ':memory:'):
            raise ValueError('archive requires a SQLite file path')
        self.path = Path(database).absolute()
        self._writer = self._connect()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        return db

    def initialize(self) -> None:
        with self.transaction() as db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS accepted_frames (
                    attempt_id TEXT PRIMARY KEY NOT NULL,
                    order_id TEXT NOT NULL UNIQUE REFERENCES orders(order_id),
                    target TEXT NOT NULL,
                    instruction_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    readings TEXT NOT NULL
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS captures (
                    result_id TEXT PRIMARY KEY NOT NULL,
                    attempt_id TEXT NOT NULL UNIQUE REFERENCES accepted_frames(attempt_id),
                    spread TEXT NOT NULL,
                    needs_review INTEGER NOT NULL CHECK (needs_review IN (0, 1))
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS receipts (
                    result_id TEXT PRIMARY KEY NOT NULL REFERENCES captures(result_id),
                    receipt_id TEXT NOT NULL UNIQUE
                )
            ''')
            db.execute('''
                CREATE VIEW IF NOT EXISTS published_captures AS
                SELECT c.result_id, c.attempt_id, f.order_id, o.shift_id, o.segment,
                       f.target, f.instruction_id, f.source, f.readings,
                       c.spread, c.needs_review, r.receipt_id
                  FROM captures AS c
                  JOIN accepted_frames AS f ON f.attempt_id = c.attempt_id
                  JOIN receipts AS r ON r.result_id = c.result_id
                  JOIN orders AS o ON o.order_id = f.order_id
            ''')

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._writer
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as db:
            db.execute('PRAGMA query_only = ON')
            yield db

    def close(self) -> None:
        self._writer.close()
