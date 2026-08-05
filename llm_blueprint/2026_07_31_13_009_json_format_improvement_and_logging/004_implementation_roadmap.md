# Implementation Roadmap: Logging & Architectural Improvements

## Introduction
This document outlines the recommended execution order for the tasks related to refactoring `tool_base` into a package, implementing NDJSON logging, and introducing granular timestamping. 

The proposed order is designed to **minimize rework** by establishing the new architectural foundation before building new features or refining existing ones on top of that foundation.

---

## Phase 1: The Foundation (Architectural Refactor)
**Primary Goal:** Transform the monolithic `tool_base.py` into a structured package and implement state management. This is the most critical step because all subsequent tasks will depend on this new architecture.

### Task 001: Refactor `tool_base` into a Package & Implement State Management
*   **Why first?** Implementing features like NDJSON or granular logging in the current monolithic structure would result in significant technical debt, as that code would need to be immediately moved and refactored once the package structure is introduced.
*   **Key Deliverables:**
    *   `tool_base/` package with `loop_states.py`.
    *   Robust signal handling (Ctrl-C returns to REPL).
    *   Centralized state management for IDLE, THINKING, OUTPUTTING, and TOOLCALLING states.

---

## Phase 2: Feature Implementation (NDJSON)
**Primary Goal:** Add the new `--logndjson` capability using the newly established architecture.

### Task 002a: Update CLI and ChatState for NDJSON
*   Add command-line arguments and update `ChatState` to support the log path and file handles within the new package structure.

### Task 002b: Implement NDJSON Writing Logic
*   Implement the logic to write conversation snapshots in valid NDJSON format, triggered at appropriate intervals during the execution loop.

---

## Phase 3: Refinement (Granular Logging)
**Primary Goal:** Enhance existing log files with high-resolution timestamps for every significant event slice.

### Task 003: Implement Granular Timestamping
*   **Why last?** This task involves refactoring the *existing* logging logic (`--outlog`, `--thoughtlog`, etc.). By doing this last, we ensure we are implementing it directly into the final, stable architecture of the `tool_base` package and its new centralized logger.
*   **Key Deliverables:**
    *   A centralized `StateLogger` that automatically handles timestamps based on state transitions (e.g., when moving from IDLE to THINKING).
    *   Granularly timestamped log files for user input, model thinking, model output, and tool calls/results.

---

## Summary Roadmap Table

| Order | Phase | Task ID | Description | Dependency |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Foundation** | `001` | Refactor `tool_base` to package + State Management | None |
| **2** | **Feature** | `002a` | CLI & ChatState setup for NDJSON | Task 001 |
| **3** | **Feature** | `002b` | Implement NDJSON writing logic | Task 002a |
| **4** | **Refinement**| `003` | Granular timestamping for all log types | Task 001 |

## Risk Mitigation Note
*   **Testing Requirement:** Every phase must include a "Regression Test" to ensure that the core functionality (running tools, chat REPL) remains intact after the architectural changes.
*   **Signal Handling Verification:** A specific focus must be placed on verifying that `Ctrl-C` behavior is correctly handled in Phase 1 before proceeding to feature implementation.
