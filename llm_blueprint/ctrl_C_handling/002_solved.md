# Ctrl-C / KeyboardInterrupt Handling — Solution

Date: 2026-08-07
Scope: `lama_ole/chat.py`, `lama_ole/tool_base/engine.py`,
`lama_ole/tool_base/loop_states.py`, `lama_ole/tool_base/__init__.py`,
`lama_ole/lama_ole.py`, `lama_ole/tests/test_ctrl_c.py`

This document describes the implementation that resolves the issues analyzed
in [`001_issues.md`](./001_issues.md). The primary goal: **a `Ctrl-C` during
any busy state (THINKING, OUTPUTTING, TOOLCALLING) must stop the current turn
and fall back to the chat prompt, never terminate the whole app.**

---

## 1. Overview of the changes

| File | Change |
|------|--------|
| `tool_base/loop_states.py` | New `ExecutionInterrupted(KeyboardInterrupt)` exception carrying the interrupted `ExecutionState`. |
| `tool_base/__init__.py` | Re-export `ExecutionInterrupted`. |
| `tool_base/engine.py` | `run_with_tools()` accepts a caller-provided `StateManager`; interrupt handlers capture the busy state, reset, and raise `ExecutionInterrupted`; interactive reads are guarded against `KeyboardInterrupt`; state is reset on the max-rounds exit paths. |
| `chat.py` | `run_chat()` collapses prompt + turn into one guarded block (no unprotected gaps), wires `state.state_manager` into the engine, prints a state-aware interrupt message, survives a second Ctrl-C during cleanup, and rolls back messages to the turn snapshot. `/feed` uses the same snapshot rollback. |
| `lama_ole.py` | The initial-content turn in chat mode (`--chat` + `-i`/`-f`/`--stdin`) catches `KeyboardInterrupt`, rolls back, and falls through to `run_chat(state)`. |
| `tests/test_ctrl_c.py` | 5 new regression tests. |

---

## 2. `tool_base/loop_states.py` — the interrupt signal

The busy-state interrupt needs to reach the REPL together with the state it
happened in. A plain `KeyboardInterrupt` cannot carry that information, so a
subclass was added:

```python
class ExecutionInterrupted(KeyboardInterrupt):
    def __init__(self, state=None):
        super().__init__()
        self.state = state
```

Because it subclasses `KeyboardInterrupt`, every existing `except
KeyboardInterrupt` keeps working. `ExecutionState`, `StateManager` and
`ExecutionInterrupted` are re-exported from `tool_base/__init__.py`.

---

## 3. `tool_base/engine.py` — the busy loop

### 3.1 Shared StateManager

`run_with_tools()` previously created a **private** `StateManager`
(`state_manager = StateManager()`) that the REPL could never observe, and
`ChatState.state_manager` was a disconnected object that was always `IDLE`.

It now accepts the caller's manager and only creates a private fallback:

```python
def run_with_tools(
    ...
    color: str = "auto",
    state_manager=None,
):
    from .loop_states import ExecutionState, StateManager, ExecutionInterrupted
    ...
    if state_manager is None:
        state_manager = StateManager()
```

This makes the transitions performed inside the engine (THINKING →
OUTPUTTING → TOOLCALLING → IDLE) observable by `ChatState`, which is the
wiring the blueprint demanded but never implemented.

### 3.2 Stream interrupt (THINKING / OUTPUTTING)

```python
except KeyboardInterrupt:
    interrupted_state = state_manager.current_state
    state_manager.reset()
    think_state = False
    print("\nInterrupted during model response. Returning to prompt.", file=sys.stderr)
    raise ExecutionInterrupted(interrupted_state)
```

The engine still cleans up (the `finally` closes the stream) and resets the
state to `IDLE`, but it now captures *which* state was active and re-raises
`ExecutionInterrupted` so the REPL can report it.

### 3.3 Tool-execution interrupt (TOOLCALLING)

```python
except KeyboardInterrupt:
    interrupted_state = state_manager.current_state
    state_manager.reset()
    print("\nInterrupted during tool execution. Returning to prompt.", file=sys.stderr)
    raise ExecutionInterrupted(interrupted_state)
```

### 3.4 Safe-mode confirmation prompt

`Ctrl-C` at the `Proceed? (y/N):` prompt is now treated as **no** (cancel the
tool) instead of propagating an unhandled interrupt:

```python
try:
    answer = sys.stdin.readline().strip().lower()
except EOFError:
    answer = 'n'
except KeyboardInterrupt:
    answer = 'n'
```

### 3.5 max_tool_rounds menu

The interactive `sys.stdin.readline()` reads in the "ask" menu were only
guarded against `EOFError`. They now also catch `KeyboardInterrupt`
(resetting the state manager and breaking out of the loop), and every exit
path from that block resets the state manager so it is never left in a stale
busy state after `run_with_tools()` returns normally.

### 3.6 One-shot / plain caller behavior

For one-shot mode (no REPL) the contract is unchanged: the interrupt still
propagates and `main()`'s top-level `except KeyboardInterrupt` decides what to
do. The new exception only adds information; it does not change the one-shot
exit behavior.

---

## 4. `chat.py` — the REPL

### 4.1 One guarded block instead of two

Before, the loop had two `try` blocks with an unprotected gap between them
(`stripped = line.strip()`, the empty check, and `messages_before =
len(state.messages)`). A `Ctrl-C` landing in that gap escaped `run_chat()` and
killed the app.

The loop now captures the snapshot **before** `input()` and guards the prompt
*and* the whole turn in a single `try`:

```python
while True:
    # Snapshot before reading input so an interrupt either at the prompt or
    # during the turn only rolls back what this iteration added.
    messages_before = len(state.messages)
    try:
        line = input(prompt)
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("/"):
            if _handle_command(stripped, state):
                break
            continue
        ...
        run_with_tools(
            ...
            state_manager=state.state_manager,
        )
    except EOFError:
        print()
        break
    except KeyboardInterrupt as e:
        ...
    except Exception as e:
        ...
```

Rolling back to a snapshot taken before `input()` is safe at the prompt too:
if nothing was typed, no messages were added, so the rollback is a no-op.

### 4.2 State-aware message and robust cleanup

```python
except KeyboardInterrupt as e:
    # A second Ctrl-C while we clean up must not kill the REPL.
    try:
        if isinstance(e, ExecutionInterrupted) and e.state not in (None, ExecutionState.IDLE):
            print(
                f"\nInterrupted during {e.state.name.lower()}. Returning to prompt.",
                file=sys.stderr,
            )
        else:
            print("\nInterrupted.")
        state.state_manager.reset()
        while len(state.messages) > messages_before:
            state.messages.pop()
    except KeyboardInterrupt:
        pass
```

- A turn interrupt prints e.g. `Interrupted during outputting. Returning to
  prompt.`; a plain prompt interrupt prints `Interrupted.`.
- The nested `try/except KeyboardInterrupt` swallows a **second** `Ctrl-C`
  raised while the handler is running, so cleanup can no longer escape the
  REPL.

### 4.3 `/feed` rollback

`_cmd_feed()` previously rolled back with a single `state.messages.pop()`,
which was wrong whenever more than one message was added during the turn
(system message, user message, assistant/tool messages). It now snapshots
before appending and rolls back to that snapshot; a `KeyboardInterrupt` rolls
back and re-raises so `run_chat()` produces the message (without double
rollback, because both snapshots are taken at the same point):

```python
messages_before = len(state.messages)
state.messages.append(user_msg)
...
try:
    run_with_tools(..., state_manager=state.state_manager)
except KeyboardInterrupt:
    while len(state.messages) > messages_before:
        state.messages.pop()
    raise
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    while len(state.messages) > messages_before:
        state.messages.pop()
```

It also now passes `toolcall_file_handle` and `chatinput_file_handle` to
`run_with_tools()`, matching the `run_chat()` call (previously missing).

---

## 5. `lama_ole.py` — initial-content chat turn (the primary bug)

When `--chat` is combined with `-i` / `-f` / `--stdin`, the first
`run_with_tools()` ran inside `main()`'s own `try`. A `Ctrl-C` there re-raised
out of the engine, hit `main()`'s `except KeyboardInterrupt: sys.exit(0)`, and
killed the app before `run_chat()` ever started.

The initial-content turn is now wrapped so an interrupt rolls back and
**falls through to the REPL**:

```python
if content.strip():
    user_msg = {"role": "user", "content": content}
    messages_before = len(state.messages)
    state.messages.append(user_msg)
    state.log_ndjson(user_msg)
    try:
        run_with_tools(
            ...
            state_manager=state.state_manager,
        )
    except KeyboardInterrupt:
        print(
            "\nInterrupted during initial response. Entering chat mode.",
            file=sys.stderr,
        )
        state.state_manager.reset()
        while len(state.messages) > messages_before:
            state.messages.pop()
run_chat(state)
```

`run_chat(state)` is now always reached, whether the initial turn completed or
was interrupted.

---

## 6. Tests (`tests/test_ctrl_c.py`)

Five regression tests, all using a fake Ollama client whose stream either
yields content (to drive the state machine into `OUTPUTTING`) or raises
`KeyboardInterrupt` mid-stream:

1. `test_run_with_tools_reraises_execution_interrupted` — the engine re-raises
   `ExecutionInterrupted` with `state == ExecutionState.OUTPUTTING`.
2. `test_run_with_tools_interrupt_is_keyboard_interrupt` — the new exception is
   still catchable as a plain `KeyboardInterrupt`.
3. `test_run_with_tools_uses_caller_state_manager` — a recording
   `StateManager` passed in observes the `OUTPUTTING`/`IDLE` transitions (the
   wiring works).
4. `test_run_chat_interrupt_during_turn_rolls_back_and_continues` — the REPL
   survives a turn interrupt, rolls `state.messages` back to the snapshot, and
   prints the state-aware message.
5. `test_main_initial_content_interrupt_falls_back_to_repl` — drives
   `main()` (chat + `-i`) with a client that raises immediately and asserts
   `run_chat()` is still entered with an empty, rolled-back conversation.

Run with:

```bash
cd lama_ole
python3 tests/run_all_tests.py
```

All suites pass (pytest + unittest).

---

## 7. Intended behavior after this fix

| Situation | Behavior |
|-----------|----------|
| `Ctrl-C` at the empty prompt | `Interrupted.` — prompt again. |
| `Ctrl-C` while thinking / streaming | `Interrupted during thinking/outputting. Returning to prompt.` — messages added this turn rolled back, prompt again. |
| `Ctrl-C` while a tool runs | `Interrupted during toolcalling. Returning to prompt.` — messages rolled back, prompt again. |
| `Ctrl-C` at the safe-mode `y/N` prompt | Treated as **no**; the dangerous tool is cancelled, the turn continues. |
| `Ctrl-C` at the max-tool-rounds menu | Menu aborted, state reset, back to the prompt. |
| `Ctrl-C` during the *initial* content turn (`--chat -i …`) | `Interrupted during initial response. Entering chat mode.` — conversation rolled back, **the REPL is entered** (previously: whole app exited). |
| `Ctrl-C` twice in a row during cleanup | Second interrupt swallowed; the REPL survives. |
| One-shot mode (no `--chat`) | Unchanged: interrupt propagates to `main()` and terminates the run. |

---

## 8. Left as future hardening

Not implemented (see the notes in `001_issues.md`):

- Item 4 — engine owns the "return to REPL" contract instead of the caller
  deciding via propagation.
- Item 8 — close the stream defensively if `client.chat()` itself raises
  before a stream object exists (the `finally` currently guards only the inner
  loop).
- Item 9 — exit code 0 on an interrupted one-shot run (conventionally 130).
