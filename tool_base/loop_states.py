from enum import Enum, auto


class ExecutionState(Enum):
    IDLE = auto()
    THINKING = auto()
    OUTPUTTING = auto()
    TOOLCALLING = auto()


class ExecutionInterrupted(KeyboardInterrupt):
    """KeyboardInterrupt raised while an execution state was active.

    Carries the ExecutionState the loop was in when the interrupt arrived so
    the caller (the REPL) can report which phase was interrupted. Raised by
    run_with_tools() after cleanup; it is a KeyboardInterrupt subclass, so
    existing handlers keep working.
    """

    def __init__(self, state=None):
        super().__init__()
        self.state = state


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
