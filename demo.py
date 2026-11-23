import argparse
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from camera.events import DispatchEvent, NoticeEvent, WriteEvent
from camera.ports import RecordingCamera
from capture.service import ShiftCaptureSession
from shift.requests import OrderRequest
from shift.spec import ShiftSpec


def run_recording(database: Path) -> None:
    recording = json.loads(Path(__file__).with_name('recording.json').read_text(encoding='utf-8'))
    spec = ShiftSpec(**recording['shift'])
    camera = RecordingCamera(tuple(recording['targets']))
    issued = {}
    dispatches = []
    with ShiftCaptureSession(database, camera) as station:
        station.open(spec)
        for operation, *args in recording['events']:
            match operation:
                case 'request':
                    station.request(OrderRequest(spec.shift_id, *args))
                case 'dispatch':
                    attempt = station.advance(DispatchEvent(*args))
                    dispatches.append((args[0], attempt.state.value))
                case 'cancel':
                    station.cancel(*args)
                case 'notice':
                    station.advance(NoticeEvent(*args))
                case 'write':
                    instruction, readings, source = args
                    station.advance(WriteEvent(instruction, tuple(readings), source))
                case 'publish':
                    issued[args[0]] = station.publish(*args)
                case _:
                    raise ValueError(f'unknown recording operation: {operation!r}')
        manifest = station.list(spec.shift_id)
        left_after_reuse = camera.read_current('left').readings
        online = station.get(issued['B'].result_id)

    with ShiftCaptureSession(database) as archive:
        offline = archive.get(issued['B'].result_id)
        downstream = archive.list(spec.shift_id)
    if offline is None or online is None:
        raise AssertionError('published B must be available by its original result number')
    actual = {
        'readings': list(offline.quality.readings), 'spread': offline.quality.spread,
        'source': offline.origin.source, 'same_receipt': offline.receipt_id == issued['B'].receipt_id,
    }
    expected = {'readings': [11, 14], 'spread': 3, 'source': 'recording/left/002', 'same_receipt': True}
    downstream_actual = {
        'orders': [item.order_id for item in downstream.entries],
        'review': [item.order_id for item in downstream.review_items],
    }
    downstream_expected = {'orders': ['B', 'C', 'D'], 'review': ['D']}
    if (actual != expected or downstream_actual != downstream_expected or offline != online
            or manifest != downstream or left_after_reuse != (20, 24)):
        raise AssertionError('recorded capture and shift handover diverged from the expected result')
    print(json.dumps({
        'dispatches': dispatches, 'manifest': asdict(downstream), 'offline_B': asdict(offline),
    }, ensure_ascii=False))
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v7', 'input': {'B': [11, 14], 'D': [20, 24], 'limit': 3},
        'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': ['capture/handoff.py', 'archive/intake.py', 'archive/lookup.py', 'archive/manifest.py'],
        'oracle': 'B was written before D. Their spreads are 3 and 4 so only D exceeds 3.',
    }, separators=(',', ':')))


def main() -> None:
    parser = argparse.ArgumentParser(description='Replay the recorded shift against a new SQLite archive.')
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument('--temporary', action='store_true')
    destination.add_argument('--database', type=Path)
    args = parser.parse_args()
    if args.temporary:
        with tempfile.TemporaryDirectory() as folder:
            run_recording(Path(folder) / 'demo.sqlite')
        return
    try:
        with args.database.open('xb'):
            pass
    except FileExistsError:
        parser.error('the demonstration database path already exists')
    run_recording(args.database)


if __name__ == '__main__':
    main()
