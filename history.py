"""Shared history model for /history, /cut and session replay.

Both history editing (``/history``, ``/cut``) and session persistence
operate on the same list of messages (``ChatState.messages``). This module
centralizes the concepts they share so the two features cannot diverge:

- entry classification (user / thinking / toolcall / tool_result / output)
- entry numbering (1 = oldest, V = newest, over non-system messages)
- selector parsing (``/history`` and ``/cut`` share the same syntax, but
  bare numbers differ: ``/history N`` shows the first N entries while
  ``/cut N`` keeps from entry N to the end — see ``parse_cut_selectors``)
- line rendering, driven by a template so ``/history`` and session replay
  look the same (each view can be tuned independently on top of a shared
  base — see ``parse_line_formats``)

Line templates are ``str.format_map`` strings with a small token set:
``{num}`` (entry number), ``{ts}`` (``[<time>] `` when the message carries a
timestamp, else empty), ``{role}`` (the display name, always present),
``{text}`` (the message text; tool calls render as a ``[data from ...]``
summary), ``{tool}`` and ``{args}`` (tool entries only). Types may be named
by their canonical key or a role-name alias (``assistant`` -> ``output``,
``tool`` -> ``tool_result``). An empty template hides that entry type.
Colors are applied per entry type whenever the global color mode is
enabled; there is no per-line color setting.
"""

import json
import os
import sys

import color_util

_FORMAT_TYPES = ("user", "output", "thinking", "toolcall", "tool_result", "compacted")
_VISIBLE_TYPES = ("user", "output", "thinking", "toolcall", "compacted")

# Role-name aliases accepted by the format spec, mapped to canonical types.
_TYPE_ALIASES = {
    "user": "user",
    "output": "output",
    "assistant": "output",
    "thinking": "thinking",
    "toolcall": "toolcall",
    "tool": "tool_result",
    "tool_result": "tool_result",
    "compacted": "compacted",
}

_DEFAULT_TEMPLATE = "[{num}] {ts}{role}: {text}"

_DEFAULT_FORMATS = {t: _DEFAULT_TEMPLATE for t in _FORMAT_TYPES}
_DEFAULT_FORMATS["tool_result"] = ""

_DEFAULT_NAMES = {
    "user": "USER",
    "assistant": "ASSISTANT",
    "thinking": "ASSISTANT (THOUGHT)",
    "toolcall": "ASSISTANT (TOOLCALL)",
    "tool": "TOOL",
}

_WARNED_TEMPLATES = set()


def classify_message(msg):
    """Classify a message dict into an entry: {'type': ..., 'msg': msg}.

    Types: user, thinking, toolcall, tool_result, output, system.
    """
    role = msg.get("role")
    if role == "system":
        return {"type": "system", "msg": msg}
    content = msg.get("content")
    if role == "user":
        return {"type": "user", "msg": msg}
    if role == "assistant":
        if isinstance(content, dict) and "thinking" in content:
            return {"type": "thinking", "msg": msg}
        if isinstance(content, str) and "thought:" in content:
            return {"type": "thinking", "msg": msg}
        if "tool_calls" in msg or (
            isinstance(content, dict) and "tool_calls" in content
        ):
            return {"type": "toolcall", "msg": msg}
        return {"type": "output", "msg": msg}
    if role == "tool":
        return {"type": "tool_result", "msg": msg}
    return {"type": "output", "msg": msg}


def history_entries(messages):
    """Numbered entries over non-system messages: 1 (oldest) .. V (newest).

    Returns a list of ``{'num', 'idx', 'type', 'msg'}`` dicts where ``idx``
    is the index into ``messages`` and ``num`` is the history number. System
    messages are excluded from the numbering so displayed numbers have no
    gaps and match the selectors accepted by ``/history`` and ``/cut``.
    """
    entries = []
    for idx, msg in enumerate(messages):
        if msg.get("role") == "system":
            continue
        entry = classify_message(msg)
        entry["num"] = len(entries) + 1
        entry["idx"] = idx
        entries.append(entry)
    return entries


def parse_selectors(arg):
    """Parse shared /history + /cut selectors into normalized ranges.

    Accepted tokens (whitespace separated), all meaning the same thing in
    ``/history`` and ``/cut``:

      N     first N entries (numbers 1..N)
      -N    last N entries (numbers V-N+1..V)
      a..b  inclusive range; either bound may be negative (-1 = newest)

    Returns a list of ``(start, end)`` tuples; ``end`` is ``None`` for the
    "-N" form (resolved against the entry count at selection time). Invalid
    tokens (non-numeric, zero) are skipped.
    """
    ranges = []
    for token in arg.split():
        if ".." in token:
            parts = token.split("..")
            if len(parts) != 2:
                continue
            try:
                a, b = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if a == 0 or b == 0:
                continue
            ranges.append((a, b))
        else:
            try:
                val = int(token)
            except ValueError:
                continue
            if val == 0:
                continue
            if val < 0:
                ranges.append((-abs(val), None))
            else:
                ranges.append((1, val))
    return ranges


def parse_cut_selectors(arg):
    """Parse /cut selectors into normalized ranges (see :func:`parse_selectors`).

    ``/cut`` trims the conversation down to the entries it names, so a bare
    positive ``N`` selects entries ``N..end`` (the older ones are removed),
    unlike ``/history`` where ``N`` means the first N entries. A bare
    ``-N`` keeps the last N entries. Ranges (``a..b``) keep only the named
    span; negative bounds count from the end (-1 = newest).

    Returns a list of ``(start, end)`` tuples where ``end`` is ``None`` for
    the bare-number forms (resolved against the entry count at selection
    time). Invalid tokens (non-numeric, zero) are skipped.
    """
    ranges = []
    for token in arg.split():
        if ".." in token:
            parts = token.split("..")
            if len(parts) != 2:
                continue
            try:
                a, b = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if a == 0 or b == 0:
                continue
            ranges.append((a, b))
        else:
            try:
                val = int(token)
            except ValueError:
                continue
            if val == 0:
                continue
            ranges.append((val, None))
    return ranges


def resolve_selectors(selectors, count):
    """Resolve parsed selectors to inclusive (start, end) entry-number spans.

    Bounds are clamped to ``[1, count]``; reversed ranges (a > b) are
    normalized by swapping. ``(a, None)`` with ``a < 0`` selects the last
    ``|a|`` entries; ``(a, None)`` with ``a > 0`` selects ``a..count`` (from
    ``/cut N``) and yields nothing when ``a`` is past the end. Returns a list
    of ``(start, end)`` tuples.
    """
    resolved = []
    for a, b in selectors:
        if b is None:
            if a < 0:
                start = count + a + 1
                end = count
            else:
                if a > count:
                    continue
                start = a
                end = count
        else:
            start = a if a > 0 else count + a + 1
            end = b if b > 0 else count + b + 1
            if start > end:
                start, end = end, start
        start = max(1, min(count, start))
        end = max(1, min(count, end))
        if start <= end:
            resolved.append((start, end))
    return resolved


def select_entry_numbers(entries, selectors):
    """History numbers (1..len(entries)) selected by the parsed selectors."""
    count = len(entries)
    numbers = set()
    for start, end in resolve_selectors(selectors, count):
        for n in range(start, end + 1):
            numbers.add(n)
    return numbers


def select_message_indices(entries, selectors):
    """``messages`` indices for the entries selected by the selectors."""
    numbers = select_entry_numbers(entries, selectors)
    return sorted(
        entries[n - 1]["idx"] for n in numbers if 1 <= n <= len(entries)
    )


def toolcall_summary(msg):
    """Human-readable summary of the tool calls in an assistant message.

    Mirrors the ``[data from <name>: <args>]`` marker that tool results are
    wrapped with, so /history shows which tool was invoked with which
    arguments without dumping the full result data.
    """
    calls = msg.get("tool_calls") or []
    parts = []
    for tc in calls:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        name = fn.get("name", "?")
        arguments = fn.get("arguments") or {}
        if isinstance(arguments, dict):
            args_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        else:
            args_str = str(arguments)
        parts.append(f"[data from {name}: {args_str}]")
    return ", ".join(parts)


def _text(content):
    if isinstance(content, dict):
        return json.dumps(content)
    return str(content)


def _apply_format_spec(formats, spec):
    """Apply one ``LAMA_OLE_FORMAT*`` value onto a resolved formats dict.

    A bare value (no ``=``) becomes the template for every visible entry
    type; otherwise semicolon-separated ``type=template`` pairs override
    individual types. Types may be named by their canonical key or a
    role-name alias (``assistant`` -> ``output``, ``tool`` ->
    ``tool_result``). An empty template (``type=``) hides that type.
    """
    if not spec:
        return
    spec = spec.strip()
    if not spec:
        return
    if "=" not in spec:
        for t in _VISIBLE_TYPES:
            formats[t] = spec
        return
    for pair in spec.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        etype = _TYPE_ALIASES.get(key.strip())
        if etype is not None:
            formats[etype] = value.strip()


_VIEW_ENV = {
    "base": "LAMA_OLE_FORMAT",
    "history": "LAMA_OLE_FORMAT_HISTORY",
    "replay": "LAMA_OLE_FORMAT_REPLAY",
}


def parse_line_formats(view="base"):
    """Resolve line templates for a view into a ``{type: template}`` dict.

    The shared ``LAMA_OLE_FORMAT`` variable applies to both /history and
    session replay; ``LAMA_OLE_FORMAT_HISTORY`` and ``LAMA_OLE_FORMAT_REPLAY``
    override it per view (``view`` is ``"history"`` or ``"replay"``; the
    default ``"base"`` reads only the shared variable). Overrides merge per
    entry type, so a view var only changes the types it names. Unset types
    keep the built-in default ``[{num}] {ts}{role}: {text}``; ``tool_result``
    stays hidden (empty template) unless a var names it.
    """
    formats = dict(_DEFAULT_FORMATS)
    _apply_format_spec(formats, os.environ.get(_VIEW_ENV["base"]))
    if view != "base":
        varname = _VIEW_ENV.get(view)
        if varname:
            _apply_format_spec(formats, os.environ.get(varname))
    return formats


def with_tool_results(formats):
    """Return ``formats`` with tool results made visible.

    Used by ``/history -t``: a hidden ``tool_result`` template is replaced by
    the default one so results render for that command only.
    """
    out = dict(formats)
    if not out.get("tool_result"):
        out["tool_result"] = _DEFAULT_TEMPLATE
    return out


def _render_template(template, tokens):
    fallback = _DEFAULT_TEMPLATE
    try:
        return template.format_map(tokens)
    except (KeyError, ValueError, IndexError) as e:
        if template not in _WARNED_TEMPLATES:
            _WARNED_TEMPLATES.add(template)
            print(
                f"Warning: invalid history template {template!r} ({e}); "
                f"falling back to the default.",
                file=sys.stderr,
            )
        return fallback.format_map(tokens)


def _first_tool(msg):
    calls = msg.get("tool_calls") or []
    for tc in calls:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        name = fn.get("name", "?")
        arguments = fn.get("arguments") or {}
        if isinstance(arguments, dict):
            args_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        else:
            args_str = str(arguments)
        return name, args_str
    return "", ""


def _type_color(etype):
    if etype == "user":
        return color_util.C_INPUT
    if etype == "thinking":
        return color_util.C_THINK
    if etype in ("toolcall", "tool_result", "compacted"):
        return color_util.C_METER_MID
    return color_util.C_OUTPUT


def format_list_entry(entry, use_color=True, formats=None):
    """Render one history entry as a line (used by /history and replay).

    The line is produced from the entry type's template (see
    :func:`parse_line_formats`); an empty template returns ``None`` (hidden).
    Tokens: ``{num}``, ``{ts}``, ``{role}``, ``{text}``, ``{tool}``,
    ``{args}``. The whole line is colored by entry type when ``use_color`` is
    set. ``formats`` defaults to the resolved shared + view configuration.
    """
    if formats is None:
        formats = parse_line_formats()
    msg = entry["msg"]
    role = msg.get("role")
    content = msg.get("content", "")

    if msg.get("compacted"):
        template = formats.get("compacted")
        etype = "compacted"
    else:
        etype = entry["type"]
        template = formats.get(etype, _DEFAULT_TEMPLATE)
    if not template:
        return None

    names = _DEFAULT_NAMES
    num = str(entry["num"])
    ts = f"[{msg['timestamp']}] " if msg.get("timestamp") else ""
    tool, args = "", ""
    if etype == "user":
        role_token = names["user"]
        text = _text(content)
    elif etype == "thinking":
        role_token = names["thinking"]
        if isinstance(content, dict) and "thinking" in content:
            thought = content["thinking"]
        else:
            thought = content
        text = _text(thought)
    elif etype == "toolcall":
        summary = toolcall_summary(msg)
        role_token = names["toolcall"]
        if summary:
            role_token = f"{role_token} {names['tool']}"
        text = summary
        tool, args = _first_tool(msg)
    elif etype == "tool_result":
        role_token = names["tool"]
        text = _text(content)
        tool = msg.get("tool_name") or ""
    elif etype == "compacted":
        role_token = "COMPACTED"
        text = (content or "").strip() or "[compacted context]"
    else:
        role_token = names.get(role, etype)
        text = _text(content)

    tokens = {
        "num": num,
        "ts": ts,
        "role": role_token,
        "text": text,
        "tool": tool,
        "args": args,
    }
    line = _render_template(template, tokens)
    return color_util.colored(line, _type_color(etype), use_color)
