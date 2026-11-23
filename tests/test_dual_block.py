import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archive.issue import PublicationError
from camera.banks import FrameOrigin
from camera.events import DispatchEvent, NoticeEvent, WriteEvent
from camera.outcomes import WriteState
from camera.page_rig import BlockReading, PageRig, ReadBlockEvent
from camera.ports import RecordingCamera
from capture.attempts import AttemptState
from capture.handoff import FrameProvenanceError
from capture.service import CaptureNotReady, ShiftCaptureSession
from shift.requests import OrderRequest
from shift.spec import ShiftSpec


class DualBlockTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'split.sqlite'
        self.camera = RecordingCamera(pages=PageRig(1))
        self.station = ShiftCaptureSession(self.path, self.camera)
        self.addCleanup(self.station.close)
        self.spec = ShiftSpec('S', 'R1', 2, 3)
        self.station.open(self.spec)

    def request(self, order_id):
        return self.station.request(OrderRequest('S', order_id, f'strip-{order_id}'))

    def dispatch(self, order_id, target='left', state=AttemptState.IN_FLIGHT):
        attempt = self.station.advance(DispatchEvent(order_id, target, f'w-{order_id}'))
        self.assertEqual(attempt.state, state)
        return attempt

    def write(self, order_id, readings):
        source = None if readings is None else f'tape/{order_id}'
        return self.station.advance(WriteEvent(f'w-{order_id}', readings, source))

    def take(self, order_id, block, status='received'):
        self.assertEqual(self.station.advance(ReadBlockEvent(f'w-{order_id}', block)), status)

    def test_read_current_refused(self):
        with self.assertRaises(ValueError):
            self.camera.read_current('left')

    def test_point_count_must_be_two_blocks(self):
        self.station.open(ShiftSpec('W', 'R1', 3, 3))
        self.assertEqual(
            self.station.request(OrderRequest('W', 'X', 'strip-X')).state, AttemptState.WAITING,
        )
        with self.assertRaises(ValueError):
            self.station.advance(DispatchEvent('X', 'left', 'w-X'))
        with self.assertRaises(KeyError):
            self.station.advance(ReadBlockEvent('w-X', 0))
        self.assertEqual(self.station.attempts.current('X').state, AttemptState.WAITING)

    def test_in_order_issue(self):
        self.request('A')
        attempt = self.dispatch('A')
        self.write('A', (11, 14))
        self.take('A', 0)
        self.take('A', 1)
        result = self.station.publish('A')
        self.assertEqual(result.quality.readings, (11, 14))
        self.assertEqual(result.quality.spread, 3)
        self.assertFalse(result.quality.needs_review)
        self.assertEqual(result.attempt_id, attempt.attempt_id)
        self.assertEqual(result.origin.source, 'tape/A')
        self.take('A', 0, 'already')
        self.take('A', 1, 'already')
        self.assertEqual(self.camera._pages.read('left:0'), None)
        self.assertEqual(self.camera._pages.read('left:1'), None)

    def test_out_of_order_and_duplicate(self):
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        self.take('A', 1)
        with self.assertRaises(CaptureNotReady):
            self.station.publish('A')
        self.take('A', 0)
        self.take('A', 0, 'already')
        self.take('A', 1, 'already')
        self.assertEqual(self.station.publish('A').quality.readings, (11, 14))

    def test_return_page_after_accept(self):
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        self.assertIsNotNone(self.camera._pages.read('left:0'))
        self.take('A', 0)
        self.take('A', 1)
        self.assertIsNone(self.camera._pages.read('left:0'))
        self.assertIsNone(self.camera._pages.read('left:1'))
        self.assertEqual(self.station.publish('A').quality.readings, (11, 14))

    def test_block_validation(self):
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        for block in (2, -1, True, '0', 0.0):
            with self.subTest(block=block), self.assertRaises(ValueError):
                self.station.advance(ReadBlockEvent('w-A', block))
        with self.assertRaises(KeyError):
            self.station.advance(ReadBlockEvent('missing', 0))

    def test_provenance_mismatch_keeps_page(self):
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        real = self.camera.outcome('w-A').origin
        wrong_block = BlockReading(real, 0, (14,))
        with patch.object(self.camera._blocks, 'read_block', return_value=wrong_block):
            with self.assertRaises(FrameProvenanceError):
                self.station.advance(ReadBlockEvent('w-A', 1))
        foreign = BlockReading(FrameOrigin('left', 'w-other', '9:9', 'x'), 1, (14,))
        with patch.object(self.camera._blocks, 'read_block', return_value=foreign):
            with self.assertRaises(FrameProvenanceError):
                self.station.advance(ReadBlockEvent('w-A', 1))
        self.assertEqual(self.camera._pages.read('left:1'), (14,))
        self.take('A', 0)
        self.take('A', 1)
        self.assertEqual(self.station.publish('A').quality.readings, (11, 14))

    def test_bad_write_length_keeps_instruction(self):
        self.request('A')
        self.dispatch('A')
        for bad in ((11,), (11, 14, 17), (11, True), ('11', 14)):
            with self.subTest(readings=bad), self.assertRaises(ValueError):
                self.write('A', bad)
        self.assertEqual(self.camera.outcome('w-A').state, WriteState.IN_FLIGHT)
        self.write('A', (11, 14))
        self.take('A', 0)
        self.take('A', 1)
        self.assertEqual(self.station.publish('A').quality.readings, (11, 14))

    def test_notice_does_not_open_window(self):
        self.request('A')
        self.dispatch('A')
        self.station.advance(NoticeEvent('w-A'))
        self.take('A', 0, 'not_ready')
        self.take('A', 1, 'not_ready')
        with self.assertRaises(CaptureNotReady):
            self.station.publish('A')
        self.write('A', (11, 14))
        self.take('A', 0)
        self.take('A', 1)
        self.assertEqual(self.station.publish('A').quality.readings, (11, 14))

    def test_waiting_when_pages_exhausted(self):
        # A keeps one taken block, B is written, so its slot plus spare fill.
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        self.take('A', 0)
        self.request('B')
        self.dispatch('B')
        self.write('B', (20, 24))
        self.request('R')
        self.dispatch('R', 'right')
        self.write('R', (30, 34))
        self.take('R', 0)
        self.request('C')
        self.dispatch('C', 'right', AttemptState.WAITING)
        self.take('A', 1)
        self.dispatch('C', 'right')
        self.write('C', (40, 44))
        for order, block in (('R', 1), ('B', 0), ('B', 1), ('C', 0), ('C', 1)):
            self.take(order, block)
        self.assertEqual(self.station.publish('A').quality.readings, (11, 14))
        self.assertEqual(self.station.publish('B').quality.readings, (20, 24))
        self.assertEqual(self.station.publish('R').quality.readings, (30, 34))
        self.assertEqual(self.station.publish('C').quality.readings, (40, 44))

    def test_b_takes_slot_without_taking_a_back_block(self):
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        self.take('A', 0)
        self.request('B')
        self.dispatch('B')
        self.write('B', (20, 24))
        self.take('A', 1)
        self.take('B', 0)
        self.take('B', 1)
        self.assertEqual(self.station.publish('A').quality.readings, (11, 14))
        self.assertEqual(self.station.publish('B').quality.readings, (20, 24))

    def test_withdraw_cancels_pending_blocks(self):
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        self.take('A', 0)
        self.assertEqual(self.station.cancel('A').state, AttemptState.WITHDRAWN)
        self.take('A', 1, 'not_ready')
        with self.assertRaises(CaptureNotReady):
            self.station.publish('A')

    def test_withdraw_while_in_flight_keeps_commitment(self):
        self.request('B')
        self.dispatch('B')
        self.station.cancel('B')
        self.write('B', (20, 24))
        self.assertEqual(self.camera.outcome('w-B').state, WriteState.WRITTEN)
        self.take('B', 0, 'not_ready')
        with self.assertRaises(CaptureNotReady):
            self.station.publish('B')
        # Pages freed once the actual terminal write landed.
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        self.take('A', 0)
        self.take('A', 1)
        self.assertEqual(self.station.publish('A').quality.readings, (11, 14))

    def test_publication_failure_then_reissue(self):
        self.request('B')
        self.dispatch('B')
        self.write('B', (20, 24))
        self.take('B', 0)
        self.take('B', 1)
        with self.station.store.transaction() as db:
            db.execute('''
                CREATE TABLE deferred_guard (
                    receipt_id TEXT REFERENCES receipts(receipt_id)
                        DEFERRABLE INITIALLY DEFERRED
                )
            ''')
            db.execute('''
                CREATE TRIGGER reject_commit AFTER INSERT ON receipts
                BEGIN
                    INSERT INTO deferred_guard VALUES ('missing');
                END
            ''')
        with self.assertRaises(PublicationError):
            self.station.publish('B')
        self.assertEqual(self.station.attempts.current('B').state, AttemptState.RETRY)
        with self.station.store.transaction() as db:
            db.execute('DROP TRIGGER reject_commit')
        result = self.station.publish('B')
        self.assertEqual(result.quality.readings, (20, 24))
        self.assertEqual(result.quality.spread, 4)
        self.assertTrue(result.quality.needs_review)

    def test_offline_retrieval(self):
        self.request('A')
        self.dispatch('A')
        self.write('A', (11, 14))
        self.take('A', 0)
        self.take('A', 1)
        issued = self.station.publish('A')
        self.station.close()
        with ShiftCaptureSession(self.path) as archive:
            self.assertEqual(archive.get(issued.result_id), issued)
            self.assertEqual([entry.order_id for entry in archive.list('S').entries], ['A'])


if __name__ == '__main__':
    unittest.main()
