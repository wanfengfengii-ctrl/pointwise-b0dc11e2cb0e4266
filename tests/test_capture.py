import tempfile
import unittest
from pathlib import Path

from camera.events import DispatchEvent, NoticeEvent, WriteEvent
from camera.outcomes import WriteState
from camera.ports import RecordingCamera
from capture.attempts import AttemptState
from capture.handoff import FrameProvenanceError
from capture.service import CaptureNotReady, ShiftCaptureSession
from shift.requests import OrderRequest
from shift.spec import ShiftSpec


class CaptureTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.camera = RecordingCamera(('left', 'right'))
        self.station = ShiftCaptureSession(Path(folder.name) / 'shift.sqlite', self.camera)
        self.addCleanup(self.station.close)
        self.spec = ShiftSpec('S', 'R1', 2, 3)

    def request(self, order_id, shift_id='S'):
        return self.station.request(OrderRequest(shift_id, order_id, f'strip-{order_id}'))

    def dispatch(self, order_id, target='left', instruction_id=None):
        return self.station.advance(DispatchEvent(order_id, target, instruction_id or f'w-{order_id}'))

    def write(self, instruction_id, readings):
        return self.station.advance(WriteEvent(instruction_id, readings, f'tape/{instruction_id}'))

    def notice(self, instruction_id):
        return self.station.advance(NoticeEvent(instruction_id))

    def test_two_banks(self):
        self.station.open(self.spec)
        self.request('A')
        self.request('B')
        a = self.dispatch('A')
        b = self.dispatch('B', 'right')
        self.assertNotEqual(a.attempt_id, b.attempt_id)
        self.write('w-B', (20, 24))
        self.write('w-A', (14, 11))
        self.notice('w-A')
        self.notice('w-B')
        second = self.station.publish('B')
        first = self.station.publish('A')
        self.assertEqual(first.quality.readings, (14, 11))
        self.assertEqual(second.quality.readings, (20, 24))
        self.assertEqual((first.origin.target, second.origin.target), ('left', 'right'))
        self.assertEqual((first.origin.source, second.origin.source), ('tape/w-A', 'tape/w-B'))
        self.assertEqual((first.attempt_id, second.attempt_id), (a.attempt_id, b.attempt_id))
        self.assertEqual([item.order_id for item in self.station.list('S').entries], ['A', 'B'])

    def test_invalid_spec(self):
        invalid = (
            ShiftSpec('', 'R1', 2, 3), ShiftSpec('S', ' ', 2, 3),
            ShiftSpec('S', 'R1', 0, 3), ShiftSpec('S', 'R1', True, 3),
            ShiftSpec('S', 'R1', 2, -1), ShiftSpec('S', 'R1', 2, 3.0),
        )
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                self.station.open(spec)
        self.assertIsNone(self.station.registry.get_shift('S'))
        self.assertIsNone(self.station.registry.get_shift(''))

    def test_open_replay(self):
        self.assertEqual(self.station.open(self.spec), self.spec)
        self.assertEqual(self.station.open(ShiftSpec('S', 'R1', 2, 3)), self.spec)
        with self.assertRaises(ValueError):
            self.station.open(ShiftSpec('S', 'R2', 3, 5))
        self.assertEqual(self.station.registry.get_shift('S'), self.spec)

    def test_frozen_spec(self):
        self.station.open(self.spec)
        pending = self.request('A')
        self.station.open(ShiftSpec('S2', 'R2', 3, 5))
        sent = self.dispatch('A')
        self.write('w-A', (11, 14))
        self.notice('w-A')
        handoff = self.station.handoffs.get('A')
        self.assertEqual(pending.order.spec, self.spec)
        self.assertEqual(sent.order.spec, self.spec)
        self.assertEqual(handoff.order.spec, self.spec)
        self.assertEqual(handoff.attempt_id, sent.attempt_id)
        self.assertEqual(self.station.publish('A').quality.spread, 3)

    def test_request_replay(self):
        self.station.open(self.spec)
        pending = self.request('A')
        self.assertEqual(self.request('A'), pending)
        going = self.dispatch('A')
        self.assertEqual(self.request('A'), going)
        self.assertEqual(self.dispatch('A'), going)
        with self.assertRaises(ValueError):
            self.station.request(OrderRequest('S', 'A', 'different-strip'))
        self.assertEqual(self.station.attempts.current('A'), going)
        self.assertEqual(self.station.registry.orders_for_shift('S'), (pending.order,))

    def test_unknown_shift(self):
        with self.assertRaises(KeyError):
            self.request('A', 'missing')
        self.assertIsNone(self.station.registry.get_order('A'))
        self.assertIsNone(self.station.attempts.current('A'))
        self.station.open(self.spec)
        self.assertEqual(self.request('A').state, AttemptState.WAITING)

    def test_notice_identity(self):
        self.station.open(self.spec)
        self.request('A')
        self.request('B')
        self.dispatch('A')
        self.dispatch('B', 'right')
        self.write('w-B', (20, 24))
        a_notice = self.notice('w-A')
        self.assertTrue(a_notice.noticed)
        self.assertEqual(a_notice.state, WriteState.IN_FLIGHT)
        self.assertEqual(self.notice('w-A'), a_notice)
        self.assertFalse(self.camera.outcome('w-B').noticed)
        self.assertIsNone(self.station.handoffs.get('A'))
        self.write('w-A', (11, 14))
        self.assertEqual(self.station.handoffs.get('A').origin.source, 'tape/w-A')
        self.notice('w-B')
        self.assertEqual(self.station.handoffs.get('B').origin.source, 'tape/w-B')

    def test_replayed_write(self):
        self.station.open(self.spec)
        self.request('A')
        self.dispatch('A')
        first = self.write('w-A', (7, 9))
        self.notice('w-A')
        self.request('B')
        sent = self.dispatch('B')
        self.write('w-B', (11, 14))
        replay = self.write('w-A', (7, 9))
        self.assertEqual(replay.origin, first.origin)
        self.assertEqual(self.camera.read_current(sent.target).readings, (11, 14))
        with self.assertRaises(ValueError):
            self.station.advance(WriteEvent('w-A', None))
        self.assertEqual(self.camera.read_current(sent.target).origin.instruction_id, 'w-B')
        self.assertEqual(self.station.publish('B').quality.readings, (11, 14))

    def test_unknown_instruction(self):
        self.station.open(self.spec)
        self.request('A')
        self.dispatch('A')
        for event in (WriteEvent('unknown', (7, 9), 'unknown'),
                      WriteEvent('unknown', None), NoticeEvent('unknown')):
            with self.subTest(event=event), self.assertRaises(KeyError):
                self.station.advance(event)
        self.assertIsNone(self.camera.read_current('left'))
        self.assertEqual(self.camera.outcome('w-A').state, WriteState.IN_FLIGHT)

    def test_cancel_with_late_write(self):
        self.station.open(self.spec)
        self.request('A')
        a = self.dispatch('A')
        self.assertEqual(self.station.cancel('A').state, AttemptState.WITHDRAWN)
        self.request('B')
        b = self.dispatch('B')
        if b.state is AttemptState.IN_FLIGHT:
            self.write('w-B', (11, 14))
            self.write('w-A', (7, 9))
        else:
            self.assertEqual(b.state, AttemptState.WAITING)
            self.write('w-A', (7, 9))
            b = self.dispatch('B')
            self.write('w-B', (11, 14))
        self.assertEqual(self.camera.outcome('w-A').origin.attempt_id, a.attempt_id)
        self.assertEqual(self.camera.outcome('w-A').state, WriteState.WRITTEN)
        self.notice('w-A')
        self.assertIsNone(self.station.handoffs.get('A'))
        with self.assertRaises(CaptureNotReady):
            self.station.publish('A')
        try:
            self.notice('w-B')
            result = self.station.publish('B')
        except (CaptureNotReady, FrameProvenanceError) as exc:
            self.fail(f'SUCCESSOR_ORIGINAL_FRAME: B has no usable original frame ({exc})')
        self.assertEqual(result.quality.readings, (11, 14), 'SUCCESSOR_ORIGINAL_FRAME')
        self.assertEqual(result.quality.spread, 3)
        self.assertEqual(result.origin.instruction_id, 'w-B')
        self.assertEqual(result.origin.attempt_id, b.attempt_id)
        self.assertEqual(result.origin.source, 'tape/w-B')
        entries = self.station.list('S').entries
        self.assertEqual([(entry.order_id, entry.receipt_id) for entry in entries], [('B', result.receipt_id)])

    def test_safe_successor(self):
        self.station.open(self.spec)
        self.request('A')
        a = self.dispatch('A')
        self.station.cancel('A')
        self.request('B')
        b = self.dispatch('B')
        if b.state is AttemptState.WAITING:
            with self.assertRaises(KeyError):
                self.camera.outcome('w-B')
        else:
            self.assertNotEqual(b.target, a.target)
        self.write('w-A', (7, 9))
        if b.state is AttemptState.WAITING:
            b = self.dispatch('B')
        self.assertEqual(b.state, AttemptState.IN_FLIGHT)
        self.write('w-B', (11, 14))
        result = self.station.publish('B')
        self.assertEqual((result.origin.target, result.quality.readings), (b.target, (11, 14)))

    def test_notice_before_terminal(self):
        self.station.open(self.spec)
        self.request('A')
        a = self.dispatch('A')
        self.station.cancel('A')
        self.assertEqual(self.notice('w-A').state, WriteState.IN_FLIGHT)
        self.request('B')
        b = self.dispatch('B')
        if b.state is AttemptState.WAITING:
            with self.assertRaises(KeyError):
                self.camera.outcome('w-B')
        else:
            self.assertNotEqual(b.target, a.target)
        self.station.advance(WriteEvent('w-A', None))
        if b.state is AttemptState.WAITING:
            b = self.dispatch('B')
        self.notice('w-B')
        self.assertIsNone(self.station.handoffs.get('B'))
        self.write('w-B', (11, 14))
        self.assertEqual(self.station.handoffs.get('B').attempt_id, b.attempt_id)

    def test_no_write(self):
        self.station.open(self.spec)
        self.request('kept')
        self.dispatch('kept')
        self.write('w-kept', (14, 11))
        self.station.publish('kept')
        self.request('A')
        first = self.dispatch('A')
        no_write = self.station.advance(WriteEvent('w-A', None))
        self.assertEqual(no_write.state, WriteState.NO_WRITE)
        self.assertEqual(self.camera.read_current('left').origin.instruction_id, 'w-kept')
        self.assertEqual(self.station.attempts.current('A').state, AttemptState.WAITING)
        second = self.dispatch('A', instruction_id='w-A-2')
        self.assertNotEqual(first.attempt_id, second.attempt_id)
        self.station.cancel('A')
        self.assertEqual(self.station.advance(WriteEvent('w-A-2', None)).state, WriteState.NO_WRITE)
        self.assertEqual(self.station.attempts.current('A').state, AttemptState.WITHDRAWN)
        self.assertEqual(self.camera.read_current('left').readings, (14, 11))
        self.request('B')
        self.assertEqual(self.dispatch('B').state, AttemptState.IN_FLIGHT)

    def test_cancel_before_publish(self):
        self.station.open(self.spec)
        self.request('A')
        self.dispatch('A')
        self.write('w-A', (7, 9))
        self.notice('w-A')
        self.assertIsNotNone(self.station.handoffs.get('A'))
        self.assertEqual(self.station.cancel('A').state, AttemptState.WITHDRAWN)
        self.assertEqual(self.station.cancel('A').state, AttemptState.WITHDRAWN)
        self.assertIsNone(self.station.handoffs.get('A'))
        with self.assertRaises(CaptureNotReady):
            self.station.publish('A')
        self.assertEqual(self.station.list('S').entries, ())

    def test_cancel_releases_written_frame_without_handoff(self):
        self.station.open(self.spec)
        self.request('A')
        self.dispatch('A')
        self.write('w-A', (7, 9))
        self.assertEqual(self.station.attempts.current('A').state, AttemptState.FRAME_READY)
        self.request('B')
        self.assertEqual(self.dispatch('B').state, AttemptState.WAITING)
        self.station.cancel('A')
        self.assertEqual(self.dispatch('B').state, AttemptState.IN_FLIGHT)
        self.notice('w-A')
        self.write('w-B', (11, 14))
        self.assertEqual(self.station.publish('B').quality.readings, (11, 14))

    def test_cancel_after_publish(self):
        self.station.open(self.spec)
        self.request('A')
        self.dispatch('A')
        self.write('w-A', (11, 14))
        published = self.station.publish('A')
        self.assertEqual(self.station.cancel('A'), published)
        self.assertEqual(self.station.get(published.result_id), published)
        self.assertEqual(self.request('A'), published)

    def test_unknown_order(self):
        self.station.open(self.spec)
        pending = self.request('A')
        with self.assertRaises(KeyError):
            self.station.cancel('missing')
        self.assertEqual(self.station.attempts.current('A'), pending)
        self.assertIsNone(self.station.registry.get_order('missing'))

    def test_independent_bank_progress(self):
        self.station.open(self.spec)
        self.request('A')
        a = self.dispatch('A')
        self.station.cancel('A')
        self.request('C')
        c = self.dispatch('C', 'right')
        self.assertEqual(c.state, AttemptState.IN_FLIGHT)
        self.write('w-C', (5, 8))
        self.notice('w-C')
        result = self.station.publish('C')
        self.assertEqual((result.origin.target, result.origin.source), ('right', 'tape/w-C'))
        self.assertEqual(result.quality.readings, (5, 8))
        self.assertEqual(self.camera.outcome(a.instruction_id).state, WriteState.IN_FLIGHT)
        self.assertEqual(self.station.list('S').entries[0].order_id, 'C')
