# Plan: History Editing and Improved Ctrl-C Handling - Part 1

## Objective
Implement "partial rollback" during a `KeyboardInterrupt` to ensure that user messages are not lost when an interaction is interrupted.

## Implementation Steps
1.  **Modify `run_chat` in `lama_ole/chat.py`**:
    *   Locate the loop where user input is read and processed.
    *   Currently, `messages_before = len(state.messages)` is taken before `input()`. This means if a user enters a message and then hits Ctrl-C during the assistant's response, the user message is also popped.
    *   Change this so that we capture the state of `state.messages` *after* the user message has been appended to `state.messages`, but *before* calling `run_with_tools`.
    *   Update the `KeyboardInterrupt` handler to pop messages until the length matches this new "post-user-input" snapshot.

## Verification
1.  Start a chat session.
2.  Type a message and press Enter.
3.  While the assistant is generating (or during tool calls), press Ctrl-C.
4.  Verify that the user's message remains in the conversation history (check with `/context` or by seeing it in the next turn).
5.  Ensure that if multiple turns were interrupted, only the last incomplete turn's messages are removed.