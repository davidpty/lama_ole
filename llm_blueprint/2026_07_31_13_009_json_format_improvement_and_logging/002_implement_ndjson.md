# Task 002: Implement NDJSON Logging

## Objective
Implement the `--logndjson <logfile>` parameter to allow saving conversation snapshots in Newline Delimited JSON (NDJSON) format.

## Subtasks

### 1. Update `ChatState` and CLI Arguments
- [ ] Add `ndjson_log_file_handle: object = None` to `ChatState` in `lama_ole/chat.py`.
- [ ] Add `--logndjson` argument to `argparse` in `lama_ole/lama_ole.py`.
- [ ] Update `main()` in `lama_ole/lama_ole.py` to open the file handle if requested and pass it to `ChatState`.

### 2. Implement NDJSON Writing Logic
- [ ] Refactor `ChatState.log_ndjson()` to write a JSON line containing:
    - `timestamp`: Current time in `%Y-%m-%d %H:%M:%S` format.
    - `model`: The current model name.
    - `message`: A single conversation message (logged as an event when it is added to the history).
- [ ] Ensure `log_ndjson()` is called whenever a message enters the conversation (user input, injected system prompt, assistant responses, tool calls and tool results) so the file is a chronological event stream rather than repeated full snapshots.

### 3. Integration and Testing
- [ ] Verify that `--logndjson` correctly creates the file and writes valid NDJSON lines.
- [ ] Test with large conversation histories to ensure performance is acceptable.
