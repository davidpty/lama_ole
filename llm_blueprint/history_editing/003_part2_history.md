# Plan: History Editing and Improved Ctrl-C Handling - Part 2

## Objective
Implement the `/history` command to allow granular inspection of the conversation history using various filtering options and range selections.

## Implementation Steps
1.  **Implement Argument Parser for `/history`**:
    *   Create a parser that can handle:
        *   Flags like `-t`.
        *   Single numbers (e.g., `10`).
        *   Negative numbers representing "from the end" (e.g., `-10` means last 10 entries, which are numbers $1$ to $10$).
        *   Ranges using `..` syntax (e.g., `5..10`, `a..b`).
        *   Multiple ranges and mixed selections (e.g., `10 -10`, `5 c..d -6`).
    *   Note: History numbers are $M$ down to $1$.

2.  **Implement `/history` Command Handler in `_handle_command`**:
    *   Call a new method `_cmd_history(arg, state)`.
    *   Use `state.get_history_entries()` to get the list of viewable entries (with their history numbers).
    *   Apply filters based on parsed arguments:
        *   If `-t` is present, ensure all entry types are included (though `get_history_entries` already seems to include them).
        *   Filter by specific history number ranges.
    *   Format and print the resulting entries to the console, showing their history numbers and content/type.

## Verification
1.  Test `/history -t` to see all messages.
2.  Test `/history 10` (first 10).
3.  Test `/history -10` (last 10).
4.  Test `/history 10 -10` (first 10 and last 10).
5.  Test complex ranges like `/history 5 c..d -6`.
6.  Verify that history numbers correctly correspond to the expected messages in `state.messages`.