"""Context-window compaction for chat sessions.

Mirrors the compaction approach used by opencode: when the context window
nears its limit, the older turns are serialized into labeled text and handed
to the model, which produces a structured anchored summary. The summary
replaces the summarized head while the most recent turns stay verbatim.

All functions in this module are pure (no I/O) so they can be unit-tested.
The streaming driver lives in ``chat.py`` (``run_compaction``).
"""

import json
import re
import time

COMPACTION_SYSTEM_PROMPT = """You are an anchored context summarization assistant for coding sessions.

Summarize only the conversation history you are given. The newest turns may be kept verbatim outside your summary, so focus on the older context that still matters for continuing the work.

If the prompt includes a <previous-summary> block, treat it as the current anchored summary. Update it with the new history by preserving still-true details, removing stale details, and merging in new facts.

Always follow the exact output structure requested by the user prompt. Keep every section, preserve exact file paths and identifiers when known, and prefer terse bullets over paragraphs.

Do not answer the conversation itself. Do not mention that you are summarizing, compacting, or merging context. Respond in the same language as the conversation."""

SUMMARY_TEMPLATE = """Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted."""

DEFAULT_TAIL_TURNS = 2
MIN_PRESERVE_RECENT_TOKENS = 2000
MAX_PRESERVE_RECENT_TOKENS = 8000
RESERVE_TOKENS = 8192
TOOL_OUTPUT_MAX_CHARS = 2000
SUMMARY_OUTPUT_TOKENS = 4096

_COMPACTED_KEY = "compacted"

_WRAPPED_RE = re.compile(
    r"^([0-9a-f]{15}) (Success|Error|Result) ([0-9a-f]{15}) (.*) ([0-9a-f]{15})$",
    re.DOTALL,
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate from character count (chars/4).

    Matches the heuristic used by the chat context meter so compaction sizing
    stays consistent with the meter's display.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def default_preserve_budget(ctx_max) -> int:
    """Token budget for the verbatim recent tail, opencode-style.

    Defaults to ``min(8000, max(2000, 25% of usable))`` where usable is the
    window minus a fixed output/overhead reserve. Falls back to the maximum
    when the window size is unknown.
    """
    if not ctx_max:
        return MAX_PRESERVE_RECENT_TOKENS
    usable = max(0, int(ctx_max) - RESERVE_TOKENS)
    target = int(usable * 0.25)
    return min(MAX_PRESERVE_RECENT_TOKENS, max(MIN_PRESERVE_RECENT_TOKENS, target))


def _truncate(value: str) -> str:
    if value is None:
        return ""
    value = str(value)
    if len(value) <= TOOL_OUTPUT_MAX_CHARS:
        return value
    return value[:TOOL_OUTPUT_MAX_CHARS] + "\n[truncated]"


def _parse_tool_message(message: dict):
    """Return (status, text) for a tool message.

    Parses the nonce-wrapped ``---BEGIN DATA---`` payload produced by the
    engine; falls back to the raw content when the wrapper is absent or
    malformed. Status is one of 'success', 'error' or 'result'.
    """
    content = message.get("content") or ""
    inner = content
    if "---BEGIN DATA---" in content and "---END DATA---" in content:
        inner = content.split("---BEGIN DATA---", 1)[1].split("---END DATA---", 1)[0]
    match = _WRAPPED_RE.match(inner.strip())
    if match:
        return match.group(2).lower(), match.group(4)
    return "result", content


def _serialize_message(message: dict) -> str:
    """Serialize one message into labeled text for the summarizer."""
    role = message.get("role")
    if role == "system":
        return ""
    if role == "user":
        content = (message.get("content") or "").strip()
        if not content:
            return ""
        return f"[User]: {content}"
    if role == "assistant":
        lines = []
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name") or "?"
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args_str = args
                else:
                    args_str = json.dumps(args)
                lines.append(f"[Assistant tool call]: {name}({args_str})")
        else:
            content = (message.get("content") or "").strip()
            if content:
                lines.append(f"[Assistant]: {content}")
        thinking = (message.get("thinking") or "").strip()
        if thinking:
            lines.append(f"[Assistant reasoning]: {thinking}")
        return "\n".join(lines)
    if role == "tool":
        status, text = _parse_tool_message(message)
        if status == "error":
            return f"[Tool error]: {_truncate(text)}"
        return f"[Tool result]: {_truncate(text)}"
    return ""


def serialize_for_compaction(messages: list) -> str:
    """Serialize a message list into labeled text for the summarizer.

    System messages are skipped (the engine re-injects the system prompt on
    every turn). Tool results are truncated to ``TOOL_OUTPUT_MAX_CHARS``.
    """
    parts = []
    for message in messages:
        text = _serialize_message(message)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def select_head_tail(messages: list, tail_turns: int = DEFAULT_TAIL_TURNS, budget=None):
    """Split non-system messages into a head to summarize and a verbatim tail.

    Turn boundaries are user messages. The most recent ``tail_turns`` turns
    are kept verbatim, bounded by ``budget`` tokens (default from
    :func:`default_preserve_budget`). The tail always starts at a user
    message, so a single turn is the smallest unit that survives compaction;
    when even the whole conversation fits under the budget the tail is the
    most recent ``tail_turns`` turns.

    Returns ``(head, tail)``, both lists excluding system messages.
    """
    non_sys = [m for m in messages if m.get("role") != "system"]
    user_indexes = [i for i, m in enumerate(non_sys) if m.get("role") == "user"]
    if not user_indexes or tail_turns <= 0:
        return non_sys, []

    if budget is None:
        budget = default_preserve_budget(None)
    budget = max(0, int(budget))

    split = len(non_sys)
    total = 0
    for i in range(len(non_sys) - 1, -1, -1):
        cost = estimate_tokens(non_sys[i].get("content") or "")
        if total + cost <= budget:
            total += cost
            split = i
        else:
            break

    if split >= len(non_sys) and user_indexes:
        split = user_indexes[-1]
    else:
        for ui in user_indexes:
            if ui >= split:
                split = ui
                break

    kept_users = [i for i in user_indexes if i >= split]
    while len(kept_users) > tail_turns:
        split = kept_users[1]
        kept_users = [i for i in user_indexes if i >= split]

    return non_sys[:split], non_sys[split:]


def find_previous_summary(messages: list):
    """Content of the most recent compacted summary message, or None."""
    for message in reversed(messages):
        if message.get("role") == "user" and message.get(_COMPACTED_KEY):
            content = (message.get("content") or "").strip()
            if content:
                return content
    return None


def build_summary_prompt(previous_summary=None, head_text: str = "") -> str:
    """Build the user prompt for the compaction summarizer call.

    Anchored: when ``previous_summary`` is given the model updates it instead
    of starting from scratch, so repeated compactions merge rather than nest.
    """
    if previous_summary:
        instruction = (
            "Update the anchored summary below using the conversation history above.\n"
            "Preserve still-true details, remove stale details, and merge in the new facts.\n"
            f"<previous-summary>\n{previous_summary}\n</previous-summary>"
        )
    else:
        instruction = "Create a new anchored summary from the conversation history."

    prompt = instruction + "\n\n" + SUMMARY_TEMPLATE
    if head_text:
        prompt += "\n\nThe following is the conversation history:\n" + head_text
    return prompt


def apply_compaction(system_messages: list, summary: str, tail: list) -> list:
    """Rebuild the message list after compaction.

    Keeps the system messages untouched, inserts the summary as a user
    message flagged ``compacted``, then appends the verbatim tail.
    """
    summary_msg = {
        "role": "user",
        "content": (summary or "").strip() or "[compacted context]",
        _COMPACTED_KEY: True,
        "summary_at": time.time(),
    }
    return list(system_messages) + [summary_msg] + list(tail)
