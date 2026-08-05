from enum import Enum, auto

class ExecutionState(Enum):
    IDLE = auto()
    THINKING = auto()
    OUTPUTTING = auto()
    TOOLCALLING = auto()


class StateManager:
    def __init__(self):
        self._current_state = ExecutionState.IDLE

    @property
    def current_state(self) -> ExecutionState:
        return self._current_state

    def transition_to(self, new_state: ExecutionState):
        """Transitions the execution state."""
        self._current_state = new_state

    def is_busy(self) -> bool:
        """Returns True if the system is in a non-IDLE state."""
        return self._current_state != ExecutionState.IDLE

    def reset(self):
        """Resets the state to IDLE."""
        self._current_state = ExecutionState.IDLE
