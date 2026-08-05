# Task 002a: Update CLI and ChatState for NDJSON

## Objective
Add the necessary infrastructure to support `--logndjson` in the command line interface and the `ChatState` object.

## Subtasks

### 1. Update CLI Arguments in `lama_ole/lama_ole.py`
- [ ] Add `--logndjson <logfile>` argument using `argparse`.
- [ ] Implement file existence check: If the specified path already exists, print an error and exit before starting the application.
- [ ] Pass the logfile path from `main()` to the `ChatState` initialization.

### 2. Update `ChatState` in `lama_ole/chat.py`
- [ ] Add `ndjson_log_path: str = None` and `ndjson_log_file_handle: object = None` to the `ChatState` dataclass.
- [ ] Implement logic to open the file handle if a path is provided.
- [ ] Ensure the file handle is properly closed in the application's cleanup/exit phase.

### 3. Integration and Testing
- [ ] Verify that `--logndjson existing_file.json` results in an error message and prevents execution.
