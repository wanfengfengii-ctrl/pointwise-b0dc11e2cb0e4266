import argparse
import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from archive.issue import PublicationError
from camera.events import DispatchEvent, NoticeEvent, WriteEvent
from camera.page_rig import BlockReading, PageRig, ReadBlockEvent
from camera.ports import RecordingCamera
from capture.attempts import AttemptState
from capture.handoff import FrameProvenanceError
from capture.service import CaptureNotReady, ShiftCaptureSession
from shift.requests import OrderRequest
from shift.spec import ShiftSpec


def expect(actual, expected):
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


@contextmanager
def raises(error):
    try:
        yield
    except error:
        return
    raise AssertionError(f"expected {error.__name__}")


def verify_fixture():
    rig = PageRig(2)
    expect(rig.page_ids, ("left:0", "left:1", "right:0", "right:1", "spare"))
    for index, page in enumerate(rig.page_ids):
        rig.write(page, (index, -index))
    with raises(KeyError):
        rig.write("extra", (0, 0))
    with raises(ValueError):
        rig.move("left:1", "spare")
    expect(rig.read("left:1"), (1, -1))
    expect(rig.read("spare"), (4, -4))
    rig.erase("spare")
    with raises(ValueError):
        rig.write("spare", (True, 4))
    expect(rig.read("spare"), None)
    rig.move("left:1", "spare")
    expect(rig.read("left:1"), None)
    expect(rig.read("spare"), (1, -1))
    print(json.dumps({page: rig.read(page) for page in rig.page_ids}))


def register(station, orders):
    station.open(ShiftSpec("split", "R17", 2, 3))
    for order in orders:
        station.request(OrderRequest("split", order, f"strip-{order}"))


def dispatch(station, order, target, state=AttemptState.IN_FLIGHT):
    result = station.advance(DispatchEvent(order, target, f"w-{order}"))
    expect(result.state, state)
    return result


def write(station, order, readings):
    source = None if readings is None else f"tape/{order}"
    return station.advance(WriteEvent(f"w-{order}", readings, source))


def take(station, order, block, status="received"):
    expect(station.advance(ReadBlockEvent(f"w-{order}", block)), status)


def reject_at_commit(station):
    with station.store.transaction() as db:
        db.execute("""
            CREATE TABLE receipt_guard (
                receipt_id TEXT REFERENCES receipts(receipt_id) DEFERRABLE INITIALLY DEFERRED
            )
        """)
        db.execute("""
            CREATE TRIGGER reject_split_commit AFTER INSERT ON receipts
            BEGIN
                INSERT INTO receipt_guard VALUES ('missing-receipt');
            END
        """)


def counts(station):
    with station.store.reader() as db:
        return tuple(db.execute("""
            SELECT (SELECT count(*) FROM accepted_frames),
                   (SELECT count(*) FROM captures), (SELECT count(*) FROM receipts)
        """).fetchone())


def run_capture(path, first):
    camera = RecordingCamera(pages=PageRig(1))
    with ShiftCaptureSession(path, camera) as station:
        register(station, ("A", "B", "R", "C", "D"))
        a = dispatch(station, "A", "left")
        station.advance(NoticeEvent("w-A"))
        take(station, "A", 0, "not_ready")
        write(station, "A", (11, 14))
        dispatch(station, "B", "left", AttemptState.WAITING)
        with raises(CaptureNotReady):
            station.publish("A")
        dispatch(station, "R", "right")
        write(station, "R", (30, 34))
        take(station, "A", 0)
        take(station, "R", 0)
        take(station, "A", 0, "already")
        with raises(CaptureNotReady):
            station.publish("A")

        b = dispatch(station, "B", "left")
        dispatch(station, "C", "right", AttemptState.WAITING)
        with raises(KeyError):
            camera.outcome("w-C")
        write(station, "B", (20, 24))
        bad = BlockReading(camera.outcome("w-B").origin, 1, (14,))
        with patch.object(camera, "advance", return_value=bad):
            with raises(FrameProvenanceError):
                station.advance(ReadBlockEvent("w-A", 1))
        dispatch(station, "C", "right", AttemptState.WAITING)

        take(station, first, 1)
        dispatch(station, "C", "right")
        write(station, "C", (40, 44))
        take(station, "B" if first == "A" else "A", 1)
        take(station, "B", 0)
        take(station, "R", 1)
        take(station, "C", 1)
        take(station, "C", 0)
        first_result = station.publish("A")
        expect(first_result.origin, camera.outcome("w-A").origin)
        expect(first_result.attempt_id, a.attempt_id)
        expect((first_result.quality.readings, first_result.quality.spread), ((11, 14), 3))

        reject_at_commit(station)
        with raises(PublicationError):
            station.publish("B")
        expect(counts(station), (1, 1, 1))
        dispatch(station, "D", "left")
        write(station, "D", (99, 98))
        with station.store.transaction() as db:
            db.execute("DROP TRIGGER reject_split_commit")
        second_result = station.publish("B")
        expect(station.publish("B"), second_result)
        take(station, "B", 1, "already")
        expect(second_result.origin, camera.outcome("w-B").origin)
        expect(second_result.attempt_id, b.attempt_id)
        expect((second_result.quality.readings, second_result.quality.spread), ((20, 24), 4))
        expect(counts(station), (2, 2, 2))
        expect(station.publish("R").quality.readings, (30, 34))
        expect(station.publish("C").quality.readings, (40, 44))

    with ShiftCaptureSession(path) as archive:
        offline_a = archive.get(first_result.result_id)
        offline_b = archive.get(second_result.result_id)
        expect((offline_a, offline_b), (first_result, second_result))
        manifest = archive.list("split")
        expect([entry.order_id for entry in manifest.entries], ["A", "B", "R", "C"])
        expect([entry.order_id for entry in manifest.review_items], ["B", "R", "C"])
    print(json.dumps({"first_back_block": first, "offline": [asdict(offline_a), asdict(offline_b)]},
                     ensure_ascii=False))


def run_withdrawal(path, readings):
    with ShiftCaptureSession(path, RecordingCamera(pages=PageRig(1))) as station:
        register(station, ("A", "R", "B", "C"))
        for order, target, original in (("A", "left", (11, 14)), ("R", "right", (30, 34))):
            dispatch(station, order, target)
            write(station, order, original)
            take(station, order, 0)
        dispatch(station, "B", "left")
        expect(station.cancel("B").state, AttemptState.WITHDRAWN)
        station.advance(NoticeEvent("w-B"))
        dispatch(station, "C", "right", AttemptState.WAITING)
        write(station, "B", readings)
        dispatch(station, "C", "right")
        write(station, "C", (40, 44))
        write(station, "B", readings)
        take(station, "B", 1, "not_ready")
        with raises(CaptureNotReady):
            station.publish("B")
        for order, original in (("A", (11, 14)), ("R", (30, 34)), ("C", (40, 44))):
            if order == "C":
                take(station, order, 0)
            take(station, order, 1)
            expect(station.publish(order).quality.readings, original)
        expect([entry.order_id for entry in station.list("split").entries], ["A", "R", "C"])


def main():
    parser = argparse.ArgumentParser(description="Replay split reads and overlapping writes.")
    parser.add_argument("mode", choices=("fixture", "verify", "demo"))
    mode = parser.parse_args().mode
    if mode == "fixture":
        verify_fixture()
        return
    with TemporaryDirectory() as folder:
        root = Path(folder)
        run_capture(root / "new-first.sqlite", "B")
        if mode == "verify":
            run_capture(root / "old-first.sqlite", "A")
            run_withdrawal(root / "late-write.sqlite", (20, 24))
            run_withdrawal(root / "no-write.sqlite", None)


if __name__ == "__main__":
    main()
