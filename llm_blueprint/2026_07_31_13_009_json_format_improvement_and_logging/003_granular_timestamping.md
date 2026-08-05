# Task 003: Refactor Logging and Implement Granular Timestamping

## Objective
Refactor the logging mechanism to use a centralized, stateful approach that provides granular timestamps for all significant events (input, thought, output, tool calls/results).

## Subtasks

### 1. Create Centralized Logger in `tool_base.py`
- [ ] Define a `StateLogger` class or similar utility in `lama_ole/tool_base.py`.
- [ ] The logger should handle:
    - Opening/closing file handles (or being passed existing ones).
    - Tracking "state slices" to avoid duplicate timestamps within the same logical block.
    - Formatting timestamps as `%Y-%m-%d %H:%M:%S`.
    - Writing specific markers for `INPUT`, `THOUGHT`, `OUTPUT`, `TOOL_CALL`, and `TOOL_RESULT`.

### 2. Refactor Existing Logging Calls in `tool_base.py`
- [ ] Replace the current "hacky" attribute-based timestamping (`getattr(handle, "_ts_written", False)`) with calls to the new `StateLogger`.
- [ ] Update `run_with_tools()` to use the logger for:
    - **Thinking**: Before and after thinking blocks.
    - **Output**: Before model response content chunks.
    - **Tool Calls**: Before tool execution starts.
    - **Tool Results**: Before writing tool results (including nonces).

### 3. Update `ChatState` and CLI Integration
- [ ] Refactor `ChatState` to hold instances of the new loggers instead of raw file handles where appropriate.
- [ ] Ensure all existing log parameters (`--thoughtlog`, `--outlog`, `--toolcalllog`, `--chatinputlog`) are updated to use the new granular timestamping logic.

### 4. Integration and Testing
- [ ] Verify that all four log types now contain timestamps before every relevant event slice.
- [ ] Ensure no regression in existing logging functionality.
