# Skills — Overview

## Purpose

A **skill** is a reusable block of text (Markdown or plain text) that is injected
into the **system role** of the conversation. It teaches the model a persona,
a capability, or a set of working instructions (e.g. "you are a code reviewer",
"always answer in German", "follow this commit-message style").

The REPL in `lama_ole/chat.py` shall support **loading and unloading** a skill
at runtime via slash commands — without restarting the process and without
losing the ongoing conversation.

## Motivation / Current Behavior

`run_with_tools()` in `tool_base/engine.py` builds the system message only when
no `system` role message exists yet:

```python
has_system = any(m.get("role") == "system" for m in messages)
if not has_system:
    sp = ""
    if system_prompt is not None:
        sp += system_prompt
        sp += "\n"
    if not no_safety_system_prompt:
        from .constants import SAFETY_SYSTEM_PROMPT, JSON_RETURN_PROMPT
        sp += SAFETY_SYSTEM_PROMPT
        sp += JSON_RETURN_PROMPT
    system_msg = {"role": "system", "content": sp}
    messages.insert(0, system_msg)
```

Consequences:

1. After the first turn the system message is **baked into `state.messages`**
   and is never refreshed.
2. `ChatState.system_prompt` is fixed at startup (`--system_prompt` /
   `--system_prompt_file`) and cannot be changed mid-session.
3. Therefore the system role cannot currently be altered at all once a
   conversation has started.

A skill loader must therefore **rewrite the existing system message in place**,
not rely on the engine's auto-insertion (which is a no-op after turn one).

## Design Principles

| Principle | Rationale |
|-----------|-----------|
| Single source of truth | The system message stored in `state.messages` is authoritative; `ChatState` never keeps a second copy that can drift. |
| Mutate in place | Load/unload edits the existing system message content; the engine's auto-insertion path is only used for the very first turn (or not at all). |
| Deterministic composition | The system content is always composed from the same ordered parts (base prompt → skill block → safety prompt), so load/unload is reversible and idempotent. |
| One active skill (v1) | A single skill slot keeps semantics simple; stacking multiple skills is a later extension (repeat the skill block per skill). |
| Plain text files | Skills are ordinary `.md`/`.txt` files — no new binary format, easy to author and diff. |
| No engine behavior change | `run_with_tools()` keeps its existing contract; only the composition logic is extracted so both sides share it. |
| Python 3.9 compatible | No 3.10+ syntax; `Optional[str]`/`List[dict]` typing as required by AGENTS.md. |

## Architecture at a Glance

```
lama_ole/
├── chat.py                       ← /skill commands + system-message rewrite
├── tool_base/
│   ├── engine.py                 ← minor refactor: extract compose_system_prompt()
│   └── constants.py              ← SAFETY_SYSTEM_PROMPT, JSON_RETURN_PROMPT (unchanged)
├── skills/                       ← skill library (new directory)
│   ├── code-reviewer.md
│   ├── german-assistant.md
│   └── ...
├── tests/                        ← test suite (pytest + unittest styles)
│   ├── test_skills.py            ← 30 skill tests
│   └── run_all_tests.py          ← runs both frameworks (see AGENTS.md)
└── documentation/skillz/
    ├── 001_overview.md           ← this file
    ├── 002_file_format.md        ← skill file layout (planned)
    ├── 003_implementation_plan.md← ordered task list (planned)
    └── 004_testing_strategy.md   ← how to verify (planned)
```

## Core Idea (one paragraph)

A skill is a text file, optionally with a small metadata header (name +
description). The REPL exposes `/skill list`, `/skill load <name>`,
`/skill unload`, and `/skill show`. Loading reads the file, composes a new
system message `base prompt + skill block + safety prompt`, and **replaces**
the existing system message in `state.messages`. Unloading recomposes the same
message **without** the skill block. Because the rewrite targets the message
that is already in the conversation, the change takes effect on the very next
turn.

## Skill File Format (v1)

```
---
name: code-reviewer
description: Review code for bugs and style.
---
You are an expert code reviewer. Focus on correctness, then style...
```

- The `---` YAML-ish front matter is optional. Without it, the filename
  (minus extension) is the skill name and there is no description.
- Everything after the front matter is the raw skill text injected into the
  system role.
- Files live in `lama_ole/skills/` by default. `/skill load` also accepts an
  absolute/relative path to a file outside the library.

## System Message Composition

The system content is always built from the same ordered parts:

```
<base system prompt from --system_prompt / --system_prompt_file, if any>

[SKILL BEGIN name=<name>]
<skill text>
[SKILL END]

<SAFETY_SYSTEM_PROMPT + JSON_RETURN_PROMPT, unless --no_safety_system_prompt>
```

To avoid duplicating this composition, extract a pure helper:

```python
# tool_base/engine.py
def compose_system_prompt(system_prompt, skill_text=None, no_safety_system_prompt=False) -> str:
    ...
```

- `run_with_tools()` calls it when it inserts a fresh system message
  (behavior unchanged — engine never knows about skills).
- `chat.py` calls the **same helper** to recompute the system message on
  load/unload, guaranteeing both paths stay in sync.

`ChatState` gains one field and one method:

```python
@dataclass
class ChatState:
    ...
    skill: Optional[str] = None          # active skill name (or path)
    skill_text: Optional[str] = None     # loaded skill content

    def apply_skill(self) -> None:
        """Rewrite the system message in state.messages from the current
        skill_text. If no system message exists yet (skill loaded before the
        first turn), compose and insert one, mirroring engine.py."""
        new_content = compose_system_prompt(
            self.system_prompt, self.skill_text, self.no_safety_system_prompt
        )
        for i, m in enumerate(self.messages):
            if m.get("role") == "system":
                m["content"] = new_content
                return
        self.messages.insert(0, {"role": "system", "content": new_content})
```

## REPL Commands

| Command | Effect |
|---|---|
| `/skill list` | List available skills (name + description) from the skills directory. |
| `/skill load <name>` | Read the skill file, set `skill_text`, call `apply_skill()`. |
| `/skill unload` | Clear `skill_text`/`skill`, call `apply_skill()` to restore the base system message. |
| `/skill show` | Print active skill name/source and a preview of its text. |

`/help` gains a line for the new commands.

## Edge Cases & Decisions

| Situation | Decision |
|---|---|
| Skill loaded before the first turn | `apply_skill()` inserts the composed system message itself; `run_with_tools()` then sees `has_system=True` and skips its own insertion. |
| Skill file missing / unreadable | Print an error; leave the active skill unchanged (no partial state). |
| Non-UTF-8 / binary skill file | Rejected: `EntropyChecker` (as `/feed` uses) is enforced on load, so binary/random files fail with a clear error. |
| `/clear` with active skill | Messages are cleared, but the skill stays loaded; the next turn rebuilds the system message with the skill still active. |
| Loading a second skill | Replaces the active one (single slot). |
| `/save` / `/load` conversations | Optionally persist `skill`/`skill_text` in the JSON; the baked system message is already saved, so loading restores identical behavior. |
| `--no_safety_system_prompt` | Composer simply omits the safety block; skill text is unaffected. |
| Tool overlap | Independent: skills only affect the system prompt; tool loading is unchanged. |

## Files to Change (Implementation Preview)

| File | Change | Status |
|---|---|---|
| `lama_ole/chat.py` | `skill`/`skill_text`/`skills_dir` fields, `apply_skill()`, `/skill` command handler + helpers, help text, save/load persistence. | Done |
| `lama_ole/tool_base/engine.py` | Extract `compose_system_prompt()`; add `skill_text` to `run_with_tools()`. | Done |
| `lama_ole/lama_ole.py` | Add `--skill` CLI arg, `LAMA_OLE_SKILL` env, `_load_skill_text()` with entropy check. | Done |
| `lama_ole/skills/*.md` | Skill library directory with example skills. | Done |
| `lama_ole/tests/test_skills.py` | Unit tests for composer, skill loading, CLI arg, REPL commands. | Done |
| `lama_ole/tests/run_all_tests.py` | Test runner: runs pytest-style **and** unittest-style tests (`python3 tests/run_all_tests.py`). Documented in `AGENTS.md` → "Running the Tests". | Done |
| `documentation/skillz/002_file_format.md` | Detailed file-format spec (front matter, escaping, limits). | Pending |
| `documentation/skillz/003_implementation_plan.md` | Ordered implementation tasks. | Pending |
| `documentation/skillz/004_testing_strategy.md` | Unit tests for the composer + REPL command tests. | Pending |

## Open Questions

1. Should skills be **stackable** (multiple active skills) in v1, or later?
   (Recommended: single slot for v1.)
2. ~~Should `/skill load` apply an entropy check, or is plain UTF-8 decoding
   enough?~~ **Resolved:** entropy check is enforced — each file must pass the
   `EntropyChecker` or loading fails.
3. ~~Should the CLI gain a `--skill <name>` startup flag so skills work in
   one-shot mode too, or is this REPL-only for now?~~ **Resolved:**
   `--skill <path>` (repeatable) + `LAMA_OLE_SKILL` env work in both one-shot
   and chat modes.

## Status

Implemented so far (2026-08-06):

- CLI `--skill <path>` parameter (repeatable; multiple files are concatenated
  with a blank line between them) works in both one-shot and chat modes. Env
  var `LAMA_OLE_SKILL` is also supported (space/comma separated, merged with
  CLI values, env-first, deduped).
- Entropy check on skill loading: each skill file must pass the `EntropyChecker`
  or loading fails with a clear error.
- `tool_base/engine.py` now exports `compose_system_prompt(system_prompt,
  skill_text, no_safety_system_prompt)`; `run_with_tools()` gained a
  `skill_text` argument. The skill block is placed between the base system
  prompt and the safety prompts, delimited by `[SKILL BEGIN]` / `[SKILL END]`.
- `ChatState` gained `skill`, `skill_text` and `skills_dir` fields plus an
  `apply_skill()` method that rewrites (or inserts) the system message.
- REPL commands `/skill list`, `/skill load <name-or-path>`, `/skill unload`
  and `/skill show` are implemented, wired into `/help`, and persist the
  active skill through `/save` / `/load`.
- A default skill library lives in `lama_ole/skills/` (`code-reviewer.md`,
  `german-assistant.md`).
- Tests: `lama_ole/tests/test_skills.py` (30 tests).
- Test runner: `lama_ole/tests/run_all_tests.py` runs the whole suite —
  both the pytest-style files and the unittest-style files
  (`test_edit_tools.py`, `test_true.py`) — via a single command. Documented
  in `AGENTS.md` → "Running the Tests".
