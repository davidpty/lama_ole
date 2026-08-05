# Task 002b: Implement NDJSON Writing Logic

## Objective
Implement the actual writing of conversation snapshots in NDJSON format.

## Subtasks

### 1. Implement Message-Event Logic in `ChatState`
- [ ] Refactor/implement `ChatState.log_ndjson(message)` to write a single JSON line per call.
- [ ] The JSON object must include:
    - `timestamp`: Current time formatted as `%Y-%m-%d %H:%M:%S`.
    - `model`: The current model name from `self.model`.
    - `message`: The single message object that was added to the conversation history.
- [ ] Ensure the output is valid NDJSON (one JSON object per line, no trailing commas between lines).

### 2. Triggering Message-Event Logging
- [ ] Log every message as it is added to the conversation instead of writing full snapshots:
    - User input in the REPL and `/feed` (`chat.py`).
    - The initial user message in one-shot/chat startup (`lama_ole.py`).
    - The injected system prompt, assistant responses (with and without tool calls) and tool results (`tool_base/engine.py` via `--logndjson` handle passed to `run_with_tools`).
- [ ] The file therefore grows by one line per message, never repeating earlier history.

### 3. Integration and Testing
- [ ] Verify that `--logndjson <file>` produces valid NDJSON files that can be parsed by `jq`.
- [ ] Test with large conversation histories to ensure memory usage remains stable (since we are writing the full history each time).
