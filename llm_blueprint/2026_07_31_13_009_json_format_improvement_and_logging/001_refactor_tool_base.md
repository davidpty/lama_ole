# Task 001: Refactor `tool_base` into a Package and Implement State Management

## Objective
Transform the monolithic `tool_base.py` into a structured package and implement a centralized state management system to support granular logging and robust signal handling (Ctrl-C).

## Subtasks

### 1. Transform `tool_base.py` into a Package
- [ ] Create directory `lama_ole/tool_base/`.
- [ ] Move existing logic from `lama_ole/tool_base.py` into appropriate modules within the new package (e.g., `__init__.py`, `decorators.py`, `registry.py`, etc.).
- [ ] Update all imports in `lama_ole/lama_ole.py` and `lama_ole/chat.py` to use the new package structure (`from tool_base import ...`).

### 2. Implement State Management in `tool_base/loop_states.py`
- [ ] Define an enumeration or set of classes representing the program's execution states:
    - `IDLE` (REPL waiting)
    - `THINKING` (Model reasoning)
    - `OUTPUTTING` (Model streaming content)
    - `TOOLCALLING` (Tool execution in progress)
- [ ] Implement a `StateManager` class that tracks the current state and provides methods for transitions.

### 3. Implement Robust Signal Handling
- [ ] Refactor the main loop in `ChatState` and `run_with_tools` to check the current state from `StateManager`.
- [ ] Wrap execution blocks (Thinking, Outputting, Toolcalling) in try/except blocks that catch `KeyboardInterrupt`.
- [ ] If a `KeyboardInterrupt` is caught while in a **busy** state (`THINKING`, `OUTPUTTING`, or `TOOLCALLING`), the handler must:
    1.  Clean up current resources (close handles, stop streams).
    2.  Reset the state to `IDLE`.
    3.  Return control to the `ChatState` REPL instead of exiting the program.

### 4. Integration and Testing
- [ ] Verify that all existing tool functionality works with the new package structure.
- [ ] Test Ctrl-C during model thinking, outputting, and tool execution to ensure it returns to the REPL without crashing.
