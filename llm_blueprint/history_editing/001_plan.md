# Plan: History Editing and Improved Ctrl-C Handling

## Objective
Improve the robustness of conversation history management during interruptions (Ctrl-C) and provide advanced history inspection and editing capabilities via `/history` and `/cut` commands in the REPL.

## 1. Refine Ctrl-C / KeyboardInterrupt Handling
**Problem:** Currently, `run_chat` pops all messages added during a turn when a `KeyboardInterrupt` occurs, including the user's input message. This results in losing the entire context of the interrupted interaction.

**Solution:** Implement "partial rollback". 
- When an interruption occurs during a tool call or assistant response, only remove the "incomplete" part of the conversation (the last assistant/tool messages that didn't reach a terminal state).
- Ensure the user message remains in `state.messages` if it was successfully received before the interruption.
- This allows `/history` to potentially show these incomplete states if they are not popped, or at least ensures the user doesn't lose their prompt.

## 2. Implement `/history` Command
**Goal:** Provide a way to inspect the conversation history with granular control and filtering.

### 2.1 Data Representation & Numbering
- **Numbering Scheme:** Messages in `state.messages` will be numbered from $M$ down to $1$, where $M = \text{len(state.messages)}$.
  - Index $0$ (oldest) $\rightarrow$ Number $M$.
  - Index $M-1$ (newest) $\rightarrow$ Number $1$.
- **Entry Types for Display:**
  - `Output`: Assistant messages with text content/responses.
  - `Thinking`: Assistant messages containing a "thinking" process.
  - `Toolcalls without results`: Assistant messages containing tool calls that are not followed by a corresponding tool response in the history (useful if we keep incomplete turns).

### 2.2 Command Syntax & Filtering
Implement a parser for the following patterns:
- `/history -t`: Show all entries, including thinking and tool calls.
- `/history -10`: Show the last 10 entries (numbers $1$ to $10$).
- `/history 10`: Show the first 10 entries (numbers $M$ down to $M-9$).
- `/history 10 -10`: Show both the first 10 and the last 10 entries.
- `/history a..b c..d ...`: Support multiple ranges (e.g., `5 c..d -6`).

### 2.3 Implementation Details
- Iterate through `state.messages` to build a list of "viewable" entries.
- Use the existing NDJSON logging logic as a reference for how message data is structured, but operate on the in-memory `state.messages`.

## 3. Implement `/cut` Command
**Goal:** Allow users to surgically remove parts of the conversation history.

### 3.1 Commands
- `/cut N`: Removes the last $N$ entries (from number $N$ down to $1$).
- `/cut a..b`: Removes a range of entries from number $a$ to number $b$.
- `/cut undo`: Restores the messages removed by the most recent `/cut` command.

### 3.2 Implementation Details
- **Undo Mechanism:** `ChatState` will maintain a `_last_cut_messages` buffer and `_last_cut_indices` (to know where to re-insert them).
- **Range Parsing:** Handle both single numbers and ranges (`a..b`).
- **Safety:** Ensure that cutting doesn't leave the conversation in an invalid state (e.g., removing a system prompt if it's at index 0, though `/cut` should probably be restricted to user/assistant messages).

## 4. Implementation Steps
1.  **Modify `ChatState`**:
    *   Add `_last_cut_messages: list = field(default_factory=list)` and `_last_cut_indices: tuple = None`.
    *   Implement `get_history_entries()` to handle numbering and entry type identification.
2.  **Update `run_chat`**:
    *   Refine the `KeyboardInterrupt` handler to implement partial rollback.
3.  **Add Command Handlers in `_handle_command`**:
    *   Implement `_cmd_history(arg, state)`.
    *   Implement `_cmd_cut(arg, state)`.
4.  **Testing**:
    *   Verify `/history` output with various range combinations.
    *   Verify `/cut` and `/cut undo` correctly modifies `state.messages`.
    *   Test Ctrl-C behavior to ensure user messages are preserved on interruption.
