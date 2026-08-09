# What `2026_08_09_18_merge_pr` brought in

Date: 2026-08-09
Branch: `2026_08_09_18_merge_pr` (unique commit `3c6c5b0` on top of the
merge-base `499f636`)

The branch is a **Chat UX** feature line. Its single unique commit
`3c6c5b0` — *"Chat UX: session persistence, context meter, plan/build modes,
colored tool-write diffs, and context compaction (#3)"* — adds ~5100 lines
across 33 files. These features then had to coexist with `main`'s history
editing during the merge (see `001_merge_report.md`).

---

## 1. Feature overview

| Area | What it does | Key code | Tests |
|------|--------------|----------|-------|
| Session persistence | Auto-save/auto-resume of chat sessions, `/resume`, `/sessions`, `/rename`, `/new` | `chat.py` sessions block, `lama_ole.py` `_resume_session_into` | `test_session.py` |
| Context-window meter | Live token gauge in the prompt, `/context`, overflow warnings | `chat.py` ctx-meter block | `test_ctx_meter.py` |
| Plan / build modes | opencode-style read-only plan mode, Shift+Tab mid-turn switching | `chat.py` plan/build block, `tool_base/mode_switch.py` | `test_mode.py`, `test_mode_switch.py` |
| Colored tool-write diffs | Unified diff shown for file writes by `edit`-family tools | `tool_base/engine.py` `_print_diff_block`, `tools/edit.py`, `color_util.py` | `test_color_util.py`, `test_edit_tools.py` |
| Context compaction | `/compact`, anchored summaries, auto-compaction on threshold | `tool_base/compaction.py`, `chat.py` `run_compaction` | `test_compaction.py` |
| Session stats | `/stats` per-round + per-model averages, persisted | `chat.py` stats block | `test_stats.py` |
| Read-only tool tagging | `__tool_readonly__` module attr drives plan mode + display | `tool_base/registry.py`, `tool_base/engine.py` | (covered by mode tests) |

---

## 2. Session persistence (`test_session.py`)

Chat runs are now **recorded automatically** and **restored automatically**:

* Storage: `~/.local/share/lama_ole/sessions/` (`XDG_DATA_HOME` /
  `LAMA_OLE_SESSION_DIR`), one directory per project (path slug + SHA-1 digest),
  one `<session-id>.json` per session with 0600 permissions.
* `serialize_session()` / `apply_session()` (shared by `/save`, `/load`,
  autosave and resume; unknown keys ignored for forward compatibility).
* Auto-resume on `--chat` start; the restored conversation is replayed
  (colors preserved, `-t`/verbose gating matching the live view). A model
  mismatch prompts session-vs-CLI choice (`_prompt_model_choice`).
* New commands: `/resume`, `/sessions`, `/rename <new title>`,
  `/rename <id-prefix> <new title>`; `/new` archives the current session;
  `/load` archives before overwriting.
* Opt-out toggles: `--no-resume` / `--no-autosave` (env
  `LAMA_OLE_RESUME=false` / `LAMA_OLE_AUTOSAVE=false`).

## 3. Context-window meter (`test_ctx_meter.py`)

* Live gauge in the prompt: `[ctx 12,345/32,768 ████░░░░░░ 37%] `, colored
  green <70%, yellow ≥70%, red ≥90% (`C_METER_LOW/MID/HIGH`).
* `/context` prints exact usage + a per-category breakdown; `/context on|off`
  toggles the meter.
* Pre-turn overflow warning when the typed message is predicted to exceed the
  window.
* Window resolution order: `--num_ctx` → `LAMA_OLE_CTX_SIZE` → running model
  context (`ollama ps`) → model `num_ctx` → declared `context_length`.
  Estimates after model changes shown with a `~`.

## 4. Plan / build modes (`test_mode.py`, `test_mode_switch.py`)

* Two opencode-style modes: **build** (default, all tools) and **plan**
  (read-only; only tools from modules marked `__tool_readonly__` are
  advertised).
* Switch with **Shift+Tab** (`ESC [ Z`), `/plan`, `/build`, or `--mode plan`
  (`LAMA_OLE_MODE=plan`). Mode shown in prompt (`[build] ` / `[plan] `) and
  remembered in saved sessions.
* **Mid-turn switching** without interrupting the response:
  `tool_base/mode_switch.py` runs fd 0 in cbreak mode during a turn and parses
  the escape sequence (`EscapeSequenceParser`, `TypeAheadBuffer`,
  `ModeHotkeyListener`). Write tools arriving after the switch are blocked and
  the error fed back to the model; printable keys typed mid-turn are replayed
  into the next prompt (`_install_typeahead_replay`, bracketed paste via
  `_install_bracketed_paste`).

## 5. Colored tool-write diffs (`test_color_util.py`, `test_edit_tools.py`)

* `run_with_tools()` gained `show_diff`; `_print_diff_block()` renders a
  **colored unified diff** after file writes (edit/create/append/apply_patch).
* `tools/edit.py` and `tools/apply_patch.py` add `_unified_diff()` /
  `_trim_diff()` to produce the diff.
* `color_util.py` reworked: `C_THINK/C_OUTPUT/C_INPUT/C_METER_*` constants,
  `parse_color_spec()` / `configure()`, `--color auto|always|never` with
  `LAMA_OLE_COLOR` env var. New `test_color_util.py`.
* CLI flag: `--diff` / `--no-diff` (`LAMA_OLE_SHOW_DIFF`), default on.

## 6. Context compaction (`test_compaction.py`)

* New pure module `tool_base/compaction.py` (no I/O, unit-testable):
  `serialize_for_compaction`, `select_head_tail`, `find_previous_summary`,
  `build_summary_prompt`, `apply_compaction`, `estimate_tokens`,
  `default_preserve_budget`, `sanitize_ctx_threshold`, plus
  `COMPACTION_SYSTEM_PROMPT` and `SUMMARY_TEMPLATE`.
* Streaming driver `run_compaction()` in `chat.py`: serializes older turns
  into labeled text, hands the head to the summarizer model, replaces it with
  a `compacted` user message while the recent tail stays verbatim; an existing
  summary is updated rather than nested.
* `/compact`, `/compact auto on|off`; auto-compaction on threshold crossing
  with confirmation. Options: `--auto-compact`, `--auto-compact-threshold`
  (0.75), `--auto-compact-model` (env `LAMA_OLE_AUTO_COMPACT*`).

## 7. Session stats (`test_stats.py`)

* `/stats`: current model, last turn's per-round breakdown (time, in/out
  tokens, tok/s) and session averages broken down per model
  (`_accumulate_stats`, `_cmd_stats`).
* Stats are serialized with the session and restored on `/resume`/`/load`.

## 8. Read-only tool tagging

* Registry reads module-level `__tool_readonly__` and records `readonly` on
  each `Tool` (`tool_base/registry.py`).
* Marked modules: `dev_tools_readonly`, `git_readonly`,
  `media_understanding_tools`, `example_tools`, `read_base64`,
  `system_locate`, `web_tools`.
* `_plan_readonly_tools()` uses the flag to build the plan-mode tool list;
  `_module_is_readonly()` gates hot-path display.

---

## 9. Ancillary changes

* `lama_ole.py`: session defaults/resume plumbing, `--mode`, `--color`,
  `--diff`, auto-compact flags; chat-mode initial-content turn now falls
  through to the REPL on interrupt.
* `chat.py`: `ChatState` grew session/ctx/compaction/stats/hotkey fields plus
  `toggle_mode`, hotkey listener API, and the readline/ctx/stats/compaction
  helper blocks (see the function inventory in `001_merge_report.md`).
* `.gitignore`: ignore `WORKFLOW*.md`. `AGENTS.md`: updated `chat.py` command
  list (`/new`, `/stats`).
* `test_chat_completion.py`, `test_ctrl_c.py` extended for the new chat loop.

---

## 10. Relation to the merge

The branch's Chat UX and `main`'s history editing touch overlapping code
(`/context` help, `_show_help`, `_cmd_feed` tail, `serialize_session`,
`run_chat`'s interrupt path, `engine.py` assistant-message construction,
`README.md` docs), which is exactly where the four conflict files came from.
After the merge both feature lines are present and all 479 tests pass — see
`001_merge_report.md` for the resolution details.
