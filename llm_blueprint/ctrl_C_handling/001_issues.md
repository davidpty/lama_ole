# Ctrl-C / KeyboardInterrupt Handling — Issue Analysis

Date: 2026-08-07
Scope: `lama_ole/chat.py`, `lama_ole/tool_base/engine.py`, `lama_ole/tool_base/loop_states.py`, `lama_ole/lama_ole.py`

## Summary

The REPL in `chat.py` is *supposed* to catch `Ctrl-C` during any busy state
(THINKING, OUTPUTTING, TOOLCALLING) and fall back to the chat prompt. This is
the documented goal of the state-management refactor
(`llm_blueprint/2026_07_31_13_009_json_format_improvement_and_logging/001_overview.md`
section C and `001_refactor_tool_base.md` section 3).

That promise only holds for turns started inside `run_chat()`. There is a
well-defined code path where `Ctrl-C` escapes all handling and reaches
`main()` in `lama_ole.py`, where `except KeyboardInterrupt: sys.exit(0)`
(line 797-798) terminates the whole app. That path is the **initial-content
turn in chat mode**. Additional structural problems make the Ctrl-C handling
fragile and make the "sometimes it exits" symptom reproducible.

---

## How Ctrl-C handling is currently wired (expected flow)

1. `run_with_tools()` (`tool_base/engine.py`) wraps the busy phases in
   `try/except KeyboardInterrupt`:
   - stream loop (THINKING / OUTPUTTING): `engine.py:223-281`, catches at
     `engine.py:277`, resets its local state manager, closes the stream in a
     `finally` (`engine.py:274-276`), then **re-raises** (`engine.py:281`).
   - tool execution (TOOLCALLING): `engine.py:339-373`, catches at
     `engine.py:370`, resets, **re-raises** (`engine.py:373`).
2. `run_chat()` (`chat.py`) catches the re-raised `KeyboardInterrupt` in its
   inner `try` (`chat.py:287-292`), prints "Interrupted.", and rolls the
   conversation back to `messages_before` (`chat.py:246`).
3. At the bare prompt, `Ctrl-C` is caught by the outer `try` around `input()`
   (`chat.py:237-239`).

So the busy-state interrupt relies entirely on **exception propagation back to
`run_chat()`**. The `StateManager` plays no role in the decision (see Finding 2).

---

## Findings

### 1. Initial-content turn in chat mode bypasses the REPL guard (primary bug)

`main()` in `lama_ole.py`, chat branch (`lama_ole.py:711-765`):

```python
if args.chat:
    state = ChatState(...)
    if content.strip():
        ...
        run_with_tools(...)      # lama_ole.py:741-764  <-- Ctrl-C lands here
    run_chat(state)              # lama_ole.py:765     <-- never reached
```

When `--chat` is combined with `-i` / `-f` / `--stdin` (initial content), the
first `run_with_tools()` is called from `main()`'s own `try` block, **not** from
`run_chat()`. If the user presses `Ctrl-C` while the model is thinking,
streaming, or calling a tool during that first turn:

- `run_with_tools()` re-raises the `KeyboardInterrupt`,
- `main()`'s `except KeyboardInterrupt: sys.exit(0)` (`lama_ole.py:797-798`)
  fires,
- the **entire app exits** and `run_chat(state)` is never entered.

This is exactly "Ctrl-C exits the complete app instead of falling back to
chatmode": the chatmode fallback never starts. It is the most likely cause of
the observed symptom because a user who supplies initial content with `--chat`
gets no working interrupt fallback for the first turn.

No rollback of the appended `user_msg` (or of the system message that
`run_with_tools` may insert) happens in this path; the process just dies.

### 2. StateManager wiring is dead / incomplete

The blueprint says `ChatState` and `run_with_tools` should *check the current
state* and only return to the REPL when a busy state was active
(`001_refactor_tool_base.md:22-27`). In practice:

- `run_with_tools()` creates its **own private** `StateManager`
  (`engine.py:105`) and never accepts one from the caller.
- `ChatState.state_manager` (`chat.py:56`) is a separate object that is
  **never transitioned** — `run_chat()` only calls `reset()` on it
  (`chat.py:289, 295`), which is a no-op because it is always `IDLE`.
- Consequently `state.state_manager` gives no information about whether a turn
  was actually in a busy state; the state machine is decorative in the
  Ctrl-C flow. The "robust signal handling" feature documented in the
  blueprint is not actually implemented — behavior is an accident of exception
  propagation, and it fails for the initial-content path (Finding 1).

### 3. `run_chat()` has unprotected gaps and a double-interrupt vulnerability

In `run_chat()` (`chat.py:225-298`), the following lines sit **between** the
outer `input()` guard and the inner `try` and are not covered by any handler:

```python
241:        stripped = line.strip()
242:        if not stripped:
243:            continue
246:        messages_before = len(state.messages)
```

A `KeyboardInterrupt` landing exactly there escapes `run_chat()` and hits
`main()` → `sys.exit(0)`.

More importantly, if a **second** `Ctrl-C` arrives while the first is being
handled inside the inner `except KeyboardInterrupt:` block (`chat.py:287-292`,
i.e. during `print` or the rollback `while` loop), that second exception is
raised inside the handler and propagates straight out of `run_chat()`. This is
a plausible "sometimes it exits" mechanism for users who hit Ctrl-C repeatedly.

### 4. `run_with_tools()` re-raises and pushes the contract to the caller

Every catch site in `engine.py` resets and **re-raises**
(`engine.py:277-281`, `engine.py:370-373`). The engine therefore never enforces
"return to REPL"; it only performs partial cleanup (state reset) and delegates
survival to the caller. That is correct for the REPL path but fatal for the
initial-content path (Finding 1) and, by design, for one-shot mode.

### 5. Unguarded interactive reads in the engine

`sys.stdin.readline()` is used without a `KeyboardInterrupt` guard at:

- `engine.py:181-184` (max_tool_rounds "ask" menu),
- `engine.py:190-196` (new round limit input),
- `engine.py:351-355` (safe-mode confirmation for dangerous tools).

Only `EOFError` is caught. A `Ctrl-C` during these prompts raises
`KeyboardInterrupt` at the engine level; it propagates to `run_chat()` (fine)
but again dies in `main()` for the initial-content path (Finding 1). The same
readline-based input is also intermixed with the REPL's `readline.input()`,
which can leave the terminal in a half-edited state after an interrupt.

### 6. `/feed` rollback is asymmetric and its handler misses KeyboardInterrupt

`_cmd_feed()` (`chat.py:378-431`):

- appends a `user_msg` (possibly plus a system message inserted by
  `run_with_tools`), then calls `run_with_tools()`;
- its error handler is `except Exception as e:` (`chat.py:429`) followed by a
  **single** `state.messages.pop()` (`chat.py:431`).

`KeyboardInterrupt` is a `BaseException`, not an `Exception`, so it is not
caught there (it bubbles up to `run_chat()`'s handler, which is fine). But on a
real `Exception` the single `pop()` is wrong whenever more than one message was
added during the turn (system message + user message + partial assistant/tool
messages). This is an error-path bug, not a Ctrl-C bug, but it shows the
rollback logic is not centralized.

### 7. No automated test coverage for Ctrl-C

No test in `lama_ole/tests/` exercises `KeyboardInterrupt` handling at all
(grep for `KeyboardInterrupt`/`Ctrl-C` finds nothing in the tests). The
blueprint explicitly required verification
(`004_implementation_roadmap.md:55` "Signal Handling Verification" must happen
in Phase 1). The regression that allows Finding 1 to exist went unnoticed
because nothing simulates a Ctrl-C on the initial-content path.

### 8. Minor: stream resource leak if `client.chat()` itself raises

The `stream.close()` guard lives in the `finally` of the **inner** `try`
(`engine.py:274-276`), i.e. inside the `for chunk in stream:` block. If
`client.chat(...)` (`engine.py:224`) raises `KeyboardInterrupt` before a stream
object is assigned, the outer `except` (`engine.py:277`) fires but `close()`
never runs — a half-open HTTP connection is not cleaned up.

### 9. Minor: Ctrl-C exits with code 0

`main()` swallows `KeyboardInterrupt` as `sys.exit(0)` (`lama_ole.py:797-798`),
reporting success. Conventionally an interrupt should surface as a non-zero
exit (e.g. 130), and scripts cannot distinguish "finished" from
"interrupted". This also masks Finding 1 in logs/scripts.

---

## Recommended fixes (in priority order)

> **Implementation status (2026-08-07):** Items 1-3, 5, 6 and 7 are
> implemented (see the commits touching `chat.py`, `lama_ole.py`,
> `tool_base/engine.py`, `tool_base/loop_states.py` and
> `tests/test_ctrl_c.py`). The engine now raises `ExecutionInterrupted`
> (a `KeyboardInterrupt` subclass) carrying the interrupted `ExecutionState`,
> and `run_with_tools()` accepts the caller's `StateManager` instead of
> creating a private one. Items 4, 8 and 9 are left as future hardening.

1. **Chat mode with initial content must fall back to the REPL**
   Wrap the initial `run_with_tools()` in `main()` (`lama_ole.py:741-764`) in a
   `try/except KeyboardInterrupt` that prints "Interrupted.", rolls back the
   appended `user_msg` (snapshot `len(state.messages)` before appending), and
   then **continues into `run_chat(state)`**. Do the same for any busy-state
   interrupt in that branch.

2. **Wire the StateManager end-to-end**
   Let `run_with_tools()` accept the caller's `StateManager` (e.g.
   `state_manager=None` parameter defaulting to a fresh one) and use it instead
   of a private instance. Have `run_chat()` pass `state.state_manager` and use
   `is_busy()` to decide the message ("Interrupted while thinking/outputting/
   toolcalling") and whether to reset. This makes the state machine real and
   the REPL fallback explicit rather than accidental.

3. **Close the gaps in `run_chat()`**
   Move `stripped = line.strip()` / empty-check / `messages_before` inside the
   guarded region, and make the rollback re-entrant so a second `Ctrl-C` during
   cleanup cannot escape (`except` handler itself wrapped or state reset first,
   then minimal work, or swallow a nested KeyboardInterrupt).

4. **Make the engine own the contract (optional but cleaner)**
   Have `run_with_tools()` return/raise a sentinel (or not re-raise) when
   interrupted in a busy state, so one-shot mode and chat mode both get
   deterministic behavior instead of caller-dependent propagation.

5. **Guard the interactive `sys.stdin.readline()` sites**
   Catch `KeyboardInterrupt` alongside `EOFError` at `engine.py:181-184`,
   `190-196`, `351-355` and treat it like "abort the menu / safe-mode prompt".

6. **Centralize rollback for `/feed`** — use the same `messages_before`
   snapshot pattern as `run_chat()` and drop the single-`pop()`.

7. **Add tests** — simulate `Ctrl-C` in a busy state (raise
   `KeyboardInterrupt` inside a fake client stream / fake tool) for both the
   REPL path and the initial-content path, asserting the REPL survives and the
   conversation rolls back correctly.

8. **Cleanup on `client.chat()` interrupt** — close the stream defensively when
   it exists even if the interrupt hit before assignment (move the `close()` to
   the outer `finally`).

9. **Exit code** — re-raise `KeyboardInterrupt` from `main()` instead of
   `sys.exit(0)`, or exit with code 130, so interrupted runs are not reported
   as success.

---

## References

- `lama_ole/chat.py` — `run_chat` (225-298), `_cmd_feed` (378-431)
- `lama_ole/tool_base/engine.py` — `run_with_tools` (78-451); busy-state
  `except KeyboardInterrupt` at 277 and 370; unguarded reads at 181-196, 351-355
- `lama_ole/tool_base/loop_states.py` — `ExecutionState` / `StateManager`
- `lama_ole/lama_ole.py` — chat branch 711-765; `except KeyboardInterrupt`
  797-798
- `llm_blueprint/2026_07_31_13_009_json_format_improvement_and_logging/`
  — `001_overview.md` (section C), `001_refactor_tool_base.md` (section 3),
    `004_implementation_roadmap.md` (line 55)
