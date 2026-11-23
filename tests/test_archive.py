import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from archive.issue import PublicationError, publish
from camera.events import DispatchEvent, NoticeEvent, WriteEvent
from camera.ports import RecordingCamera
from capture.attempts import AttemptState
from capture.service import CaptureNotReady, ShiftCaptureSession
from quality.shape import FrameShapeError
from shift.requests import OrderRequest
from shift.spec import ShiftSpec


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'shift.sqlite'
        self.camera = RecordingCamera()
        self.station = ShiftCaptureSession(self.path, self.camera)
        self.addCleanup(self.station.close)
        self.spec = ShiftSpec('S', 'R1', 2, 3)
        self.station.open(self.spec)

    def request(self, order_id):
        return self.station.request(OrderRequest('S', order_id, f'strip-{order_id}'))

    def frame(self, order_id, readings, target='left'):
        self.request(order_id)
        instruction = f'w-{order_id}'
        sent = self.station.advance(DispatchEvent(order_id, target, instruction))
        self.assertEqual(sent.state, AttemptState.IN_FLIGHT)
        self.station.advance(WriteEvent(instruction, readings, f'tape/{instruction}'))
        self.station.advance(NoticeEvent(instruction))
        return self.station.handoffs.get(order_id)

    def sql(self, *statements):
        with closing(sqlite3.connect(self.path)) as db, db:
            for statement in statements:
                db.execute(statement)

    def counts(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('''
                SELECT (SELECT count(*) FROM accepted_frames),
                       (SELECT count(*) FROM captures),
                       (SELECT count(*) FROM receipts)
            ''').fetchone()

    def fail_receipt_insert(self):
        self.sql('''
            CREATE TRIGGER reject_first_receipt BEFORE INSERT ON receipts
            BEGIN
                SELECT RAISE(ABORT, 'receipt unavailable');
            END
        ''')

    def test_threshold_equality(self):
        self.frame('B', (11, 14))
        issued = self.station.publish('B')
        received = self.station.get(issued.result_id)
        expected = {'readings': [11, 14], 'spread': 3, 'needs_review': False}
        actual = {
            'readings': list(received.quality.readings), 'spread': received.quality.spread,
            'needs_review': received.quality.needs_review,
        }
        reviews = [item.order_id for item in self.station.list('S').review_items]
        self.assertEqual(actual, expected)
        self.assertEqual(reviews, [])
        self.assertEqual(received.origin.source, 'tape/w-B')
        self.assertEqual(received.receipt_id, issued.receipt_id)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v2', 'input': {'readings': [11, 14], 'limit': 3},
            'expected': expected, 'actual': actual,
            'downstream_expected': [], 'downstream_actual': reviews,
            'code_paths': ['quality/judgment.py', 'quality/spread.py', 'archive/manifest.py'],
            'oracle': '14 minus 11 equals the inclusive limit 3, leaving no review item',
        }, separators=(',', ':')))

    def test_original_order(self):
        self.frame('B', (14, 11))
        result = self.station.publish('B')
        reread = self.station.get(result.result_id)
        self.assertEqual(reread.quality.readings, (14, 11))
        self.assertEqual(reread.quality.spread, 3)

    def test_exact_signed_large_ticks(self):
        bound = 1 << 15000
        wide = ShiftSpec('S-wide', 'RW', 2, 2 * bound)
        self.station.open(wide)
        self.station.request(OrderRequest(wide.shift_id, 'wide', 'strip-wide'))
        self.station.advance(DispatchEvent('wide', 'left', 'w-wide'))
        self.station.advance(WriteEvent('w-wide', (bound, -bound), 'tape/w-wide'))
        issued = self.station.publish('wide')
        with ShiftCaptureSession(self.path) as archive:
            quality = archive.get(issued.result_id).quality
            self.assertEqual(quality.readings, (bound, -bound))
            self.assertEqual(quality.spread, 2 * bound)
            self.assertFalse(quality.needs_review)
            self.assertEqual(archive.list(wide.shift_id).entries[0].spread, 2 * bound)

    def assert_rejected(self, order_id, readings, message):
        frame = self.frame(order_id, readings)
        with self.assertRaisesRegex(FrameShapeError, message):
            self.station.publish(order_id)
        self.assertIsNone(self.station.get(f'capture/{frame.attempt_id}'))
        self.assertEqual(self.station.attempts.current(order_id).state, AttemptState.REJECTED)
        self.assertIsNone(self.station.handoffs.get(order_id))
        self.assertEqual(self.station.list('S').entries, ())
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_reject_empty_frame(self):
        self.assert_rejected('empty', (), 'empty frame')

    def test_wrong_length(self):
        self.assert_rejected('short', (11,), 'point count')
        self.assert_rejected('long', (11, 14, 17), 'point count')

    def test_non_integer(self):
        for index, invalid in enumerate((None, True, 14.0, '14')):
            with self.subTest(reading=invalid):
                self.assert_rejected(f'bad-{index}', (11, invalid), 'not an integer tick')

    def test_retry_after_commit_failure(self):
        self.sql('''
            CREATE TABLE deferred_receipt_guard (
                receipt_id TEXT REFERENCES receipts(receipt_id) DEFERRABLE INITIALLY DEFERRED
            )
        ''', '''
            CREATE TRIGGER reject_commit AFTER INSERT ON receipts
            BEGIN
                INSERT INTO deferred_receipt_guard VALUES ('missing-receipt');
            END
        ''')
        frame = self.frame('B', (11, 14))
        with self.assertRaises(PublicationError) as error:
            self.station.publish('B')
        self.assertIsInstance(error.exception.__cause__, sqlite3.IntegrityError)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertIsNone(self.station.get(f'capture/{frame.attempt_id}'))
        self.assertEqual(self.station.list('S').entries, ())
        self.assertEqual(self.station.attempts.current('B').state, AttemptState.RETRY)
        self.assertIs(self.station.handoffs.get('B'), frame)
        self.sql('DROP TRIGGER reject_commit')
        issued = self.station.publish('B')
        self.assertEqual(issued.quality.readings, (11, 14))
        self.assertEqual(issued.origin, frame.origin)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.station.publish('B'), issued)

    def test_no_half_item(self):
        self.frame('kept', (14, 11))
        old = self.station.publish('kept')
        frame = self.frame('B', (11, 14))
        self.fail_receipt_insert()
        with self.assertRaises(PublicationError):
            self.station.publish('B')
        with ShiftCaptureSession(self.path) as archive:
            self.assertIsNone(archive.get(f'capture/{frame.attempt_id}'))
            self.assertEqual(archive.get(old.result_id), old)
            self.assertEqual([entry.order_id for entry in archive.list('S').entries], ['kept'])
        self.assertEqual(self.counts(), (1, 1, 1))
        self.sql('DROP TRIGGER reject_first_receipt')
        self.assertEqual(self.station.publish('B').origin.source, 'tape/w-B')
        self.assertEqual(self.counts(), (2, 2, 2))

    def test_retry_same_handoff(self):
        frame = self.frame('B', (11, 14))
        self.fail_receipt_insert()
        with self.assertRaises(PublicationError):
            self.station.publish('B')
        self.assertIs(self.station.handoffs.get('B'), frame)
        self.frame('D', (20, 24))
        self.assertEqual(self.camera.read_current('left').readings, (20, 24))
        self.sql('DROP TRIGGER reject_first_receipt')
        issued = self.station.publish('B')
        self.assertEqual(issued.attempt_id, frame.attempt_id)
        self.assertEqual(issued.origin, frame.origin)
        self.assertEqual(issued.quality.readings, (11, 14))
        self.assertIsNone(self.station.handoffs.get('B'))
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_cancel_prevents_retry_after_failure(self):
        self.frame('B', (11, 14))
        self.fail_receipt_insert()
        with self.assertRaises(PublicationError):
            self.station.publish('B')
        self.station.cancel('B')
        self.sql('DROP TRIGGER reject_first_receipt')
        with self.assertRaises(CaptureNotReady):
            self.station.publish('B')
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_replay_after_commit(self):
        frame = self.frame('B', (11, 14))
        issued = self.station.publish('B')
        self.assertEqual(publish(self.station.store, frame), issued)
        self.assertEqual(self.station.publish('B'), issued)
        with ShiftCaptureSession(self.path) as archive:
            self.assertEqual(archive.publish('B'), issued)
            self.assertEqual(archive.cancel('B'), issued)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_reopen_after_bank_reuse(self):
        b_frame = self.frame('B', (11, 14))
        issued = self.station.publish('B')
        self.frame('D', (20, 24))
        self.assertEqual(self.camera.read_current('left').readings, (20, 24))
        self.station.publish('D')
        online = self.station.get(issued.result_id)
        self.assertIsNotNone(online, 'ARCHIVED_ORIGINAL_FRAME')
        self.assertEqual(online.quality.readings, (11, 14), 'ARCHIVED_ORIGINAL_FRAME')
        self.station.close()
        with ShiftCaptureSession(self.path) as archive:
            self.assertIsNone(archive.camera)
            offline = archive.get(issued.result_id)
            self.assertIsNotNone(offline, 'ARCHIVED_ORIGINAL_FRAME')
            self.assertEqual(offline.quality.readings, (11, 14), 'ARCHIVED_ORIGINAL_FRAME')
            self.assertEqual(offline.quality.spread, 3)
            self.assertEqual(offline.origin, b_frame.origin)
            self.assertEqual(offline.receipt_id, issued.receipt_id)
            self.assertEqual(offline, issued)
            self.assertEqual([entry.order_id for entry in archive.list('S').review_items], ['D'])

    def test_idempotent_init(self):
        custom = ShiftSpec('user-shift', 'user-card-17', 2, 9)
        order = OrderRequest(custom.shift_id, 'user-order', 'user-segment')
        self.station.open(custom)
        pending = self.station.request(order)
        self.station.advance(DispatchEvent(order.order_id, 'left', 'user-write'))
        self.station.advance(WriteEvent('user-write', (-3, 6), 'user/recorded-17'))
        original = self.station.publish(order.order_id)
        self.station.store.initialize()
        self.station.registry.initialize()
        with ShiftCaptureSession(self.path) as reopened:
            self.assertEqual(reopened.registry.get_shift(custom.shift_id), custom)
            self.assertEqual(reopened.registry.get_order(order.order_id), pending.order)
            recovered = reopened.get(original.result_id)
            self.assertEqual(recovered, original)
            self.assertEqual(recovered.quality.readings, (-3, 6))
            self.assertEqual(recovered.origin.source, 'user/recorded-17')
            items = reopened.list(custom.shift_id).entries
            self.assertEqual([(item.order_id, item.receipt_id) for item in items],
                             [(order.order_id, original.receipt_id)])
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_unknown_result(self):
        self.assertIsNone(self.station.get('capture/unknown'))

    def test_shift_manifest(self):
        self.request('B')
        self.request('C')
        self.request('D')
        self.request('waiting')
        self.station.cancel('C')
        self.frame('D', (20, 24), 'right')
        d = self.station.publish('D')
        self.frame('B', (11, 14))
        b = self.station.publish('B')
        self.frame('unpublished', (1, 2))
        self.assertEqual(self.station.list('S').spec, self.spec)
        manifest = self.station.list('S')
        self.assertEqual([entry.order_id for entry in manifest.entries], ['B', 'D'])
        self.assertEqual([entry.result_id for entry in manifest.entries], [b.result_id, d.result_id])
        self.assertEqual([entry.receipt_id for entry in manifest.entries], [b.receipt_id, d.receipt_id])
        self.assertEqual([(item.order_id, item.spread) for item in manifest.review_items], [('D', 4)])

    def test_known_empty_shift(self):
        manifest = self.station.list('S')
        self.assertEqual(manifest.spec, self.spec)
        self.assertEqual(manifest.entries, ())
        self.assertEqual(manifest.review_items, ())

    def test_unknown_shift(self):
        self.assertIsNone(self.station.list('missing'))
