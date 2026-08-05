# Overview: Logging and Timestamping Improvements

## Problem Statement
The current logging mechanism in `lama_ole` has several limitations regarding granularity and traceability:

1.  **Lack of Granularity**: Existing log files (`--outlog`, `--thoughtlog`, `--chatinputlog`, `--toolcalllog`) only receive a timestamp at the very beginning of the file. This makes it impossible to correlate specific parts of a conversation (e.g., a particular tool call or a specific thought process) with a precise time, especially in long-running chat sessions.
2.  **Missing NDJSON Support**: There is no way to export the full conversation state in a machine-readable format like Newline Delimited JSON (NDJSON), which would be useful for downstream analysis or training data preparation.
3.  **Inconsistent Timestamping**: The logic for writing timestamps is currently scattered and uses an unconventional method of attaching attributes (`_ts_written`) directly to file handles, which is error-prone and difficult to maintain.

## Proposed Improvements

### 1. NDJSON Logging (`--logndjson <logfile>`)
We will implement a new command-line parameter `--logndjson` that allows users to save the conversation as a stream of NDJSON events. Each line in the file represents a single message that was added to the conversation (timestamp, model name, and the message object), making it easy to parse with standard tools like `jq` and ready for downstream analysis or training data preparation.

### 2. Granular Timestamping
We will refactor the logging logic to ensure that every significant "state slice" is preceded by a timestamp. This includes:
- **User Input**: Before each chat input slice (already partially implemented).
- **Model Thinking**: Before each block of model thinking/reasoning.
- **Model Output**: Before each chunk of model response content.
- **Tool Calls & Results**: Before every tool call and its subsequent result.

### 3. Architectural Improvements
To support these changes and improve the robustness of the application (especially regarding signal handling), we will undergo a significant refactoring of the core logic:

#### A. Refactoring `tool_base` into a Package
The current monolithic `tool_base.py` will be transformed into a Python package (`tool_base/`). This allows for better separation of concerns and easier management of complex state transitions.

#### B. Centralized State Management (`loop_states.py`)
We will introduce `tool_base/loop_states.py` to manage the execution state of the program loop. The application will transition through several explicit states:
*   **IDLE**: Waiting for user input in the REPL.
*   **THINKING**: Model is generating a thought process.
*   **OUTPUTTING**: Model is streaming response content.
*   **TOOLCALLING**: A tool has been invoked and is awaiting/processing results.

#### C. Robust Signal Handling (Ctrl-C / KeyboardInterrupt)
A major goal of the state management refactor is to fix the current issue where `Ctrl-C` during a "busy" state (Thinking, Outputting, or Toolcalling) causes the entire program to exit. 
By using the new `loop_states.py`, we will implement logic such that:
*   If a `KeyboardInterrupt` occurs while in a **busy** state, the current execution loop is interrupted and cleaned up, but control is returned to the `ChatState` REPL instead of terminating the process.

#### D. Centralized Logging Logic
Move timestamping and formatting logic into a dedicated utility within the new `tool_base/` package. This logger will interact with the state manager to ensure timestamps are written correctly at the start of every logical "slice" (e.g., when transitioning from IDLE to THINKING).

## File Safety Policy
To prevent accidental data loss, the following rules apply to all logging parameters:
*   **Existing Files**: The parameters `--outlog`, `--toolcalllog`, `--chatinputlog`, `--thoughtlog`, and the new `--logndjson` **must not overwrite existing files**. If a specified file already exists, the application must refuse to start and display an error message.

## Summary of Changes
| Feature | Current State | Proposed State |
| :--- | :--- | :--- |
| **NDJSON Export** | Not available | `--logndjson <file>` (Per-message events) |
| **Timestamping** | Once per file | Before every input, thought, output, and tool event |
| **Architecture** | Monolithic `tool_base.py` | Package `tool_base/` with `loop_states.py` |
| **Ctrl-C Handling** | Exits program during busy states | Returns to REPL when interrupted in busy states |
| :--- | :--- | :--- |
| **NDJSON Export** | Not available | `--logndjson <file>` (Per-message events) |
| **Timestamping** | Once per file | Before every input, thought, output, and tool event |
| **Implementation** | Scattered/Attribute-based | Centralized/Logger-based |
