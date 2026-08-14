"""Shared history model for /history, /cut and session replay.

Both history editing (``/history``, ``/cut``) and session persistence
operate on the same list of messages (``ChatState.messages``). This module
centralizes the concepts they share so the two features cannot diverge:

- entry classification (user / thinking / toolcall / tool_result / output)
- entry numbering (1 = oldest, V = newest, over non-system messages)
- selector parsing (``/history`` and ``/cut`` share the same syntax, but
  bare numbers differ: ``/history N`` shows the first N entries while
  ``/cut N`` keeps from entry N to the end — see ``parse_cut_selectors``)
- line rendering, driven by per-view templates so ``/history`` and session
  replay can be tuned independently (see ``parse_line_formats`` and
  ``parse_output_format``)

Line templates are ``str.format_map`` strings with a small token set:
``{num}`` (entry number), ``{ts}`` (``[<time>] `` when the message carries a
timestamp, else empty), ``{role}`` (the display name, always present),
``{text}`` (the message text; tool calls render as a ``[data from ...]``
summary), ``{tool}`` and ``{args}`` (tool entries only). Types may be named
by their canonical key or a role-name alias (``assistant`` -> ``output``,
``tool`` -> ``tool_result``). An empty template hides that entry type.
Display names can be customized with ``name.<role>=<label>`` pairs, which
override what the ``{role}`` token expands to (roles: ``user``, ``assistant``,
``thinking``, ``toolcall``, ``tool``, ``compacted``). Colors are applied per
entry type whenever the global color mode is enabled; there is no per-line
color setting.
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
_DEFAULT_OUTPUT_TEMPLATE = "{text}"

_DEFAULT_FORMATS = {t: _DEFAULT_TEMPLATE for t in _FORMAT_TYPES}
_DEFAULT_FORMATS["tool_result"] = ""

_DEFAULT_NAMES = {
    "user": "USER",
    "assistant": "ASSISTANT",
    "thinking": "ASSISTANT (THOUGHT)",
    "toolcall": "ASSISTANT (TOOLCALL)",
    "tool": "TOOL",
    "compacted": "COMPACTED",
}

_NAME_ROLES = tuple(_DEFAULT_NAMES)

_WARNED_TEMPLATES = set()


class LineFormats(dict):
    """Resolved line templates plus the role names used by the ``{role}`` token.

    Behaves like a plain ``{type: template}`` dict so callers and tests keep
    indexing ``formats[...]``. The ``names`` attribute holds the per-role
    display names and ``_bare`` remembers the effective bare-value template
    (used by ``with_tool_results`` so ``/history -t`` keeps tool-result lines
    in the same style as the rest).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.names = dict(_DEFAULT_NAMES)
        self._bare = None


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


def _parse_selectors(arg, cut_mode=False):
    """Parse shared selector tokens into normalized ranges.

    Accepted tokens (whitespace separated): ``N``, ``-N`` (last N entries,
    ``a..b`` (inclusive; either bound may be negative, -1 = newest).
    The two commands differ only in what a bare positive ``N`` means:
    ``/history`` shows the first N entries (``(1, N)``) while ``/cut`` keeps
    from entry N to the end (``(N, None)``) — that difference is the
    ``cut_mode`` flag.

    Returns a list of ``(start, end)`` tuples; ``end`` is ``None`` for the
    bare-number forms (resolved against the entry count at selection time).
    Invalid tokens (non-numeric, zero) are skipped.
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
                ranges.append((val, None) if cut_mode else (1, val))
    return ranges


def parse_selectors(arg):
    """Parse /history selectors into normalized ranges (see :func:`_parse_selectors`).

    ``/history N`` shows the first N entries; ``-N`` the last N; ``a..b`` an
    inclusive range with negative bounds counting from the end (-1 = newest).
    Returns a list of ``(start, end)`` tuples; ``end`` is ``None`` for the
    "-N" form (resolved against the entry count at selection time). Invalid
    tokens (non-numeric, zero) are skipped.
    """
    return _parse_selectors(arg, cut_mode=False)


def parse_cut_selectors(arg):
    """Parse /cut selectors into normalized ranges (see :func:`_parse_selectors`).

    ``/cut`` trims the conversation down to the entries it names, so a bare
    positive ``N`` selects entries ``N..end`` (the older ones are removed),
    unlike ``/history`` where ``N`` means the first N entries. A bare ``-N``
    keeps the last N entries. Ranges (``a..b``) keep only the named span;
    negative bounds count from the end (-1 = newest).

    Returns a list of ``(start, end)`` tuples where ``end`` is ``None`` for
    the bare-number forms (resolved against the entry count at selection
    time). Invalid tokens (non-numeric, zero) are skipped.
    """
    return _parse_selectors(arg, cut_mode=True)


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
    """Apply one format value onto a resolved formats dict.

    A bare value (no ``=``) becomes the template for every visible entry
    type; otherwise semicolon-separated ``type=template`` pairs override
    individual types. Types may be named by their canonical key or a
    role-name alias (``assistant`` -> ``output``, ``tool`` ->
    ``tool_result``). ``name.<role>=<label>`` pairs override the display
    names the ``{role}`` token expands to. An empty template (``type=``)
    hides that type.
    """
    if not spec:
        return
    spec = spec.strip()
    if not spec:
        return
    if "=" not in spec:
        for t in _VISIBLE_TYPES:
            formats[t] = spec
        formats._bare = spec
        return
    for pair in spec.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip()
        if key.startswith("name."):
            role = key[len("name."):]
            if role in _NAME_ROLES:
                formats.names[role] = value.strip()
            continue
        etype = _TYPE_ALIASES.get(key)
        if etype is not None:
            formats[etype] = value.strip()


_VIEW_ENV = {
    "history": "LAMA_OLE_FORMAT_HISTORY",
    "replay": "LAMA_OLE_FORMAT_REPLAY",
    "output": "LAMA_OLE_FORMAT_OUTPUT",
}


def parse_line_formats(view="history"):
    """Resolve line templates for a view into a :class:`LineFormats` dict.

    Each view reads only its own environment variable:
    ``LAMA_OLE_FORMAT_HISTORY``, ``LAMA_OLE_FORMAT_REPLAY`` or
    ``LAMA_OLE_FORMAT_OUTPUT``. Overrides merge per entry type (and per role
    name), so a view var only changes the types and names it mentions. Unset
    types keep the built-in default ``[{num}] {ts}{role}: {text}``;
    ``tool_result`` stays hidden (empty template) unless a var names it.
    """
    formats = LineFormats(_DEFAULT_FORMATS)
    varname = _VIEW_ENV.get(view)
    if varname:
        _apply_format_spec(formats, os.environ.get(varname))
    return formats


def parse_output_format():
    """Resolve the live chat output template.

    The live assistant stream keeps its current raw-text behavior unless
    ``LAMA_OLE_FORMAT_OUTPUT`` is set. The same template tokens are available
    as the history/replay views, but the default is the plain streamed text
    rather than a numbered listing.
    """
    formats = LineFormats({"output": _DEFAULT_OUTPUT_TEMPLATE})
    _apply_format_spec(formats, os.environ.get(_VIEW_ENV["output"]))
    return formats


def _clone_line_formats(formats):
    """Copy a LineFormats (or plain dict) preserving names and the bare template."""
    out = LineFormats(formats)
    out.names = dict(getattr(formats, "names", _DEFAULT_NAMES))
    out._bare = getattr(formats, "_bare", None)
    return out


def with_tool_results(formats):
    """Return ``formats`` with tool results made visible.

    Used by ``/history -t``: a hidden ``tool_result`` template is replaced by
    the effective bare-value template (so the results match the rest of the
    listing), falling back to the default one otherwise.
    """
    out = _clone_line_formats(formats)
    if not out.get("tool_result"):
        out["tool_result"] = out._bare or _DEFAULT_TEMPLATE
    return out


def _render_template(template, tokens):
    fallback = _DEFAULT_TEMPLATE
    try:
        return template.format_map(tokens)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError) as e:
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


def _split_text_template(template):
    marker = "{text}"
    idx = template.find(marker)
    if idx < 0:
        return template, ""
    return template[:idx], template[idx + len(marker):]


def format_output_entry(entry, use_color=True, formats=None):
    """Render the live assistant output line prefix/suffix for chat mode.

    Returns a ``(prefix, suffix)`` tuple. The prefix is printed before the
    first streamed content chunk and the suffix after the stream ends. When
    the template is just ``{text}`` the helper returns an empty prefix and
    suffix, preserving the current raw-output behavior.
    """
    if formats is None:
        formats = parse_output_format()
    template = formats.get("output", _DEFAULT_OUTPUT_TEMPLATE)
    if not template:
        return None

    msg = entry["msg"]
    role = msg.get("role")
    content = msg.get("content", "")
    names = getattr(formats, "names", _DEFAULT_NAMES)
    etype = entry.get("type") or "output"
    role_token = names.get(role, etype)
    num = str(entry["num"])
    ts = f"[{msg['timestamp']}] " if msg.get("timestamp") else ""
    tokens = {
        "num": num,
        "ts": ts,
        "role": role_token,
        "text": _text(content),
        "tool": "",
        "args": "",
    }
    prefix_template, suffix_template = _split_text_template(template)
    prefix = _render_template(prefix_template, tokens)
    suffix = _render_template(suffix_template, tokens) if suffix_template else ""
    color = _type_color("output")
    return (
        color_util.colored(prefix, color, use_color),
        color_util.colored(suffix, color, use_color),
    )


def format_list_entry(entry, use_color=True, formats=None):
    """Render one history entry as a line (used by /history and replay).

    The line is produced from the entry type's template (see
    :func:`parse_line_formats`); an empty template returns ``None`` (hidden).
    Tokens: ``{num}``, ``{ts}``, ``{role}``, ``{text}``, ``{tool}``,
    ``{args}``. The whole line is colored by entry type when ``use_color`` is
    set. ``formats`` defaults to the resolved per-view configuration.
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

    names = getattr(formats, "names", _DEFAULT_NAMES)
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
        role_token = names.get("compacted", "COMPACTED")
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
