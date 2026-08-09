# Plan: History Editing and Improved Ctrl-C Handling - Part 3

## Objective
Implement the `/cut` command to allow users to surgically remove parts of the conversation history, including an undo mechanism.

## Implementation Steps
1.  **Implement `/cut` Command Handler in `_handle_command`**:
    *   Call a new method `_cmd_cut(arg, state)`.

2.  **Implement `_cmd_cut(arg, state)` Logic**:
    *   Parse the argument to determine the type of cut:
        *   `/cut undo`: Calls `state.undo_cut()`.
        *   `/cut N` (where $N$ is a number): Removes entries from history number $N$ down to $1$.
        *   `/cut a..b` (range): Removes entries in the range of history numbers $a$ to $b$.
    *   **Crucial**: History numbers are $M$ down to $1$. We must map these back to `state.messages` indices correctly.

3.  **Implement Range Parsing and Mapping Logic**:
    *   Create a helper function to parse the argument into one or more ranges of history numbers.
    *   Map history number ranges to `state.messages` index slices.
    *   Ensure that cutting doesn't remove system messages (index 0).

4.  **Implement Undo Mechanism**:
    *   Before performing a cut, store the removed messages in `state._last_cut_messages`.
    *   Store the indices where they were removed from in `state._last_cut_indices` to allow re-insertion via `undo_cut()`.

## Verification
1.  Test `/cut 5`: Removes last 5 entries (numbers $1$ through $5$).
2.  Test `/cut 5..10`: Removes entries with history numbers $5, 6, 7, 8, 9, 10$.
3.  Test `/cut undo`: Restores the messages removed by the last successful `/cut`.
4.  Verify that `state.messages` is correctly updated and subsequent turns work as expected.