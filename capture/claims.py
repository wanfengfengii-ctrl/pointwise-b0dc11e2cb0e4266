from dataclasses import dataclass, replace

from camera.outcomes import WriteOutcome, WriteState


@dataclass(frozen=True)
class _Claim:
    instruction_id: str
    write_state: WriteState = WriteState.IN_FLIGHT
    withdrawn: bool = False


class TargetClaims:
    """Owns target reuse decisions while the device owns physical execution."""

    def __init__(self):
        self._claims: dict[str, _Claim] = {}

    def available(self, target: str) -> bool:
        return target not in self._claims

    def claim(self, target: str, instruction_id: str) -> None:
        if not self.available(target):
            raise ValueError(f'target still has an owner: {target!r}')
        self._claims[target] = _Claim(instruction_id)

    def observe_outcome(self, outcome: WriteOutcome) -> None:
        claim = self._claims.get(outcome.target)
        if claim is None or claim.instruction_id != outcome.instruction_id:
            return
        if outcome.state is WriteState.IN_FLIGHT:
            return
        if claim.withdrawn or outcome.state is WriteState.NO_WRITE:
            del self._claims[outcome.target]
        else:
            self._claims[outcome.target] = replace(claim, write_state=outcome.state)

    def withdraw(self, target: str, instruction_id: str) -> None:
        claim = self._claims.get(target)
        if claim is None or claim.instruction_id != instruction_id:
            return
        if claim.write_state is WriteState.IN_FLIGHT:
            self._claims[target] = replace(claim, withdrawn=True)
        else:
            del self._claims[target]

    def release_frame(self, target: str, instruction_id: str) -> None:
        claim = self._claims.get(target)
        if (claim is None or claim.instruction_id != instruction_id
                or claim.write_state is not WriteState.WRITTEN or claim.withdrawn):
            raise ValueError('only the owner of a settled frame can release its target')
        del self._claims[target]
