from dataclasses import dataclass

from camera.banks import FrameOrigin


@dataclass(frozen=True)
class ReadBlockEvent:
    instruction_id: str
    block: int


@dataclass(frozen=True)
class BlockReading:
    origin: FrameOrigin
    block: int
    readings: tuple[int, ...]


class PageRig:
    """Five raw device pages with a fixed width for this recording."""

    page_ids = ("left:0", "left:1", "right:0", "right:1", "spare")

    def __init__(self, block_size: int):
        if type(block_size) is not int or block_size < 1:
            raise ValueError("block_size must be a positive integer")
        self.block_size = block_size
        self._pages: dict[str, tuple[int, ...] | None] = dict.fromkeys(self.page_ids)

    def read(self, page: str) -> tuple[int, ...] | None:
        return self._pages[page]

    def write(self, page: str, readings: tuple[int, ...]) -> None:
        if self._pages[page] is not None:
            raise ValueError(f"page is occupied: {page}")
        block = tuple(readings)
        if len(block) != self.block_size or any(type(value) is not int for value in block):
            raise ValueError("page requires one block of integer ticks")
        self._pages[page] = block

    def erase(self, page: str) -> None:
        self.read(page)
        self._pages[page] = None

    def move(self, source: str, destination: str) -> None:
        block = self.read(source)
        destination_block = self.read(destination)
        if block is None or destination_block is not None:
            raise ValueError("move requires an occupied source and an empty destination")
        self._pages[destination] = block
        self._pages[source] = None
