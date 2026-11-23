from dataclasses import dataclass, field

from camera.banks import FrameOrigin
from camera.events import NoticeEvent, WriteEvent
from camera.outcomes import OutcomeLog, WriteOutcome, WriteState
from camera.page_rig import BlockReading, PageRig, ReadBlockEvent
from camera.writes import AcceptedWrite

BLOCK_COUNT = 2


@dataclass
class _BlockJob:
    instruction_id: str
    order_id: str
    segment: str
    attempt_id: str
    target: str
    state: WriteState = WriteState.IN_FLIGHT
    origin: FrameOrigin | None = None
    blocks: dict[int, str] = field(default_factory=dict)
    withdrawn: bool = False


class DualBlockExecutor:
    """Device-side keeper for the five raw pages of a dual-block recording.

    Each slot owns two pages per block position and shares one spare page.  An
    accepted instruction owes a two-block write; while it is in flight that
    commitment counts two pages, and once written each still-untaken block
    counts one.  A dispatch is accepted only when the whole set of live
    commitments can still be packed into the five pages, so an accepted write
    is always honourable.  A withdrawal ends the right to take blocks but an
    in-flight write commitment survives to its actual terminal event.  Pages
    are picked and moved at write time, which keeps the shared page free for
    whichever slot needs it as soon as a taken block makes room.
    """

    def __init__(self, pages: PageRig, targets: tuple[str, ...]):
        self._pages = pages
        self._targets = tuple(targets)
        self._outcomes = OutcomeLog()
        self._jobs: dict[str, _BlockJob] = {}
        self._owner: dict[str, tuple[str, int]] = {}

    @property
    def block_size(self) -> int:
        return self._pages.block_size

    def _own_pages(self, target: str) -> tuple[str, str]:
        return (f'{target}:0', f'{target}:1')

    def _in_flight(self, target: str) -> int:
        return sum(
            1 for job in self._jobs.values()
            if job.target == target and job.state is WriteState.IN_FLIGHT
        )

    def _position_counts(self) -> dict[tuple[str, int], int]:
        """Blocks needing a page per (slot, block position).

        A block at slot ``S`` position ``p`` may occupy only ``S:p`` or the one
        shared spare, so this is the precise capacity view.  An in-flight
        instruction still owes both positions, even after a withdrawal, until
        its actual terminal write or no-write.
        """
        counts: dict[tuple[str, int], int] = {}
        for job in self._jobs.values():
            if job.state is WriteState.IN_FLIGHT:
                positions = range(BLOCK_COUNT)
            elif job.state is WriteState.WRITTEN and not job.withdrawn:
                positions = tuple(job.blocks)
            else:
                continue
            for position in positions:
                key = (job.target, position)
                counts[key] = counts.get(key, 0) + 1
        return counts

    @staticmethod
    def _packable(counts: dict[tuple[str, int], int]) -> bool:
        """Each position has one own page; the five-page rig has one spare."""
        deficits = sum(max(0, count - 1) for count in counts.values())
        return deficits <= 1

    def _fits_with(self, target: str) -> bool:
        counts = self._position_counts()
        for position in range(BLOCK_COUNT):
            key = (target, position)
            counts[key] = counts.get(key, 0) + 1
        return self._packable(counts)

    def _compact(self) -> None:
        """Move a shared-page block back to its own slot's matching page.

        Own pages of a slot can only hold that slot's blocks, so returning a
        block to its position page is a safe relocation that frees the shared
        page for the other slot.
        """
        for page, (instruction_id, block) in list(self._owner.items()):
            if page != 'spare':
                continue
            job = self._jobs[instruction_id]
            destination = f'{job.target}:{block}'
            if destination in self._owner or self._pages.read(destination) is not None:
                continue
            self._pages.move('spare', destination)
            del self._owner['spare']
            self._owner[destination] = (instruction_id, block)
            job.blocks[block] = destination

    def accept(self, write: AcceptedWrite) -> AcceptedWrite | None:
        if not isinstance(write.instruction_id, str) or not write.instruction_id.strip():
            raise ValueError('instruction_id must be a nonblank string')
        existing = self._jobs.get(write.instruction_id)
        if existing is not None:
            if (existing.order_id, existing.segment, existing.attempt_id, existing.target) != (
                write.order_id, write.segment, write.attempt_id, write.target,
            ):
                raise ValueError(f'conflicting dispatch for {write.instruction_id!r}')
            return AcceptedWrite(
                existing.instruction_id, existing.order_id, existing.segment,
                existing.attempt_id, existing.target,
            )
        if write.target not in self._targets:
            raise KeyError(write.target)
        if self._in_flight(write.target):
            return None
        if not self._fits_with(write.target):
            return None
        self._outcomes.register(write.instruction_id, write.target, write.attempt_id)
        self._jobs[write.instruction_id] = _BlockJob(
            write.instruction_id, write.order_id, write.segment, write.attempt_id, write.target,
        )
        return write

    def _discard(self, job: _BlockJob) -> None:
        for page in job.blocks.values():
            self._owner.pop(page, None)
            if self._pages.read(page) is not None:
                self._pages.erase(page)
        job.blocks.clear()

    def _validate(self, readings: object) -> tuple[int, ...]:
        if not isinstance(readings, (tuple, list)):
            raise ValueError('a dual-block write needs two complete blocks')
        ticks = tuple(readings)
        if len(ticks) != BLOCK_COUNT * self.block_size:
            raise ValueError('a dual-block write needs two complete blocks')
        if any(type(tick) is not int for tick in ticks):
            raise ValueError('a dual-block write needs integer ticks')
        return ticks

    def _allocate(self, target: str) -> tuple[str, str]:
        """Place each block on its own position page, else the shared spare.

        ``<slot>:<block>`` is the canonical page for that block position; the
        shared spare may carry one block of either slot when the canonical
        page is still held by an earlier untaken block.
        """
        self._compact()
        placement: list[str] = []
        spare_used = False
        for block in range(BLOCK_COUNT):
            own = f'{target}:{block}'
            if own not in self._owner and self._pages.read(own) is None:
                placement.append(own)
                continue
            if spare_used or 'spare' in self._owner or self._pages.read('spare') is not None:
                raise RuntimeError('device cannot honour a committed two-block write')
            placement.append('spare')
            spare_used = True
        return placement[0], placement[1]

    def execute(self, event: WriteEvent) -> WriteOutcome:
        job = self._jobs.get(event.instruction_id)
        if job is None:
            raise KeyError(event.instruction_id)
        outcome = self._outcomes.get(event.instruction_id)
        requested = WriteState.NO_WRITE if event.readings is None else WriteState.WRITTEN
        if job.state is not WriteState.IN_FLIGHT:
            if job.state is not requested:
                raise ValueError(f'conflicting terminal state for {event.instruction_id!r}')
            return outcome
        if event.readings is None:
            job.state = WriteState.NO_WRITE
            return self._outcomes.settle(event.instruction_id, WriteState.NO_WRITE)
        ticks = self._validate(event.readings)
        if not isinstance(event.source, str) or not event.source.strip():
            raise ValueError('a physical write needs a nonblank recording source')
        first, second = self._allocate(job.target)
        self._pages.write(first, ticks[:self.block_size])
        self._pages.write(second, ticks[self.block_size:])
        self._owner[first] = (job.instruction_id, 0)
        self._owner[second] = (job.instruction_id, 1)
        job.blocks = {0: first, 1: second}
        job.state = WriteState.WRITTEN
        job.origin = FrameOrigin(job.target, job.instruction_id, job.attempt_id, event.source)
        if job.withdrawn:
            self._discard(job)
            self._compact()
        return self._outcomes.settle(event.instruction_id, WriteState.WRITTEN, job.origin)

    def read_block(self, event: ReadBlockEvent) -> BlockReading | None:
        job = self._jobs.get(event.instruction_id)
        if job is None:
            raise KeyError(event.instruction_id)
        if type(event.block) is not int or event.block not in (0, 1):
            raise ValueError('block must be integer 0 or 1')
        if job.state is not WriteState.WRITTEN or job.withdrawn:
            return None
        page = job.blocks.get(event.block)
        if page is None:
            return None
        readings = self._pages.read(page)
        if readings is None:
            return None
        return BlockReading(job.origin, event.block, readings)

    def return_block(self, instruction_id: str, block: int) -> None:
        job = self._jobs.get(instruction_id)
        if job is None:
            return
        page = job.blocks.pop(block, None)
        if page is None:
            return
        self._owner.pop(page, None)
        if self._pages.read(page) is not None:
            self._pages.erase(page)
        self._compact()

    def withdraw(self, instruction_id: str) -> None:
        job = self._jobs.get(instruction_id)
        if job is None:
            return
        job.withdrawn = True
        if job.state is WriteState.WRITTEN:
            self._discard(job)
            self._compact()

    def notify(self, event: NoticeEvent) -> WriteOutcome:
        return self._outcomes.record_notice(event.instruction_id)

    def outcome(self, instruction_id: str) -> WriteOutcome:
        return self._outcomes.get(instruction_id)
