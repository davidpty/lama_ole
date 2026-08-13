#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from ollama import Client

# Ensure the script's directory is in sys.path for sibling imports
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Ensure the current working directory is in sys.path for user tool modules
_cwd = os.getcwd()
if _cwd not in sys.path:
    sys.path.insert(0, _cwd)

from tool_base import (
    get_tool_modules_info,
    load_tools,
    set_ollama_host,
    set_vision_models,
    to_openai_tools,
    run_with_tools,
    sanitize_ctx_threshold,
    DEFAULT_CTX_COMPACT_THRESHOLD,
)
from backends import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_LLAMACPP_HOST,
    create_router,
)
import color_util
from chat import (
    ChatState,
    _drop_incomplete_trailing_messages,
    _replay_history,
    apply_session,
    autosave_session,
    find_recent_session,
    new_session_id,
    run_chat,
)

# ---------------------------------------------------------------------------
# Configuration defaults (env vars + config files)
# ---------------------------------------------------------------------------

_ENV_FILE_USER = os.path.join(os.path.expanduser("~"), ".config", "lama_ole", "lama_ole.env")
_ENV_FILE_PROJECT = os.path.join(os.getcwd(), "lama_ole.env")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_env_file(path):
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            values[key] = value
    return values


def load_env_files():
    """Load LAMA_OLE_* defaults from config files into os.environ.

    Precedence: existing shell env vars are never overwritten; the project
    file (./lama_ole.env in the CWD) overrides the user file
    (~/.config/lama_ole/lama_ole.env). Empty values are ignored.
    """
    merged = {}
    for path in (_ENV_FILE_USER, _ENV_FILE_PROJECT):
        merged.update(_parse_env_file(path))
    for key, value in merged.items():
        if value == "":
            continue
        os.environ.setdefault(key, value)


def _env_str(name, default):
    value = os.environ.get(name)
    if not value:
        return default
    return value


def _env_int(name, default):
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        print(f"Warning: ignoring invalid integer for {name}: {value!r}",
              file=sys.stderr)
        return default


def _env_float(name, default):
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        print(f"Warning: ignoring invalid number for {name}: {value!r}",
              file=sys.stderr)
        return default


def _env_bool(name, default):
    value = os.environ.get(name)
    if not value:
        return default
    lowered = value.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    print(f"Warning: ignoring invalid boolean for {name}: {value!r}",
          file=sys.stderr)
    return default


def _deprecated_env(legacy_name, new_name):
    """Print a migration warning for a still-honored legacy env var."""
    print(
        f"Warning: {legacy_name} is deprecated; use {new_name} instead.",
        file=sys.stderr,
    )


def _ollama_host_env():
    """Resolve the Ollama host from the environment.

    ``LAMA_OLE_HOST_OLLAMA`` is the canonical variable. The legacy
    ``LAMA_OLE_HOST`` is still honored so existing configs keep working.
    """
    value = os.environ.get("LAMA_OLE_HOST_OLLAMA")
    if value:
        return value
    legacy = os.environ.get("LAMA_OLE_HOST")
    if legacy:
        _deprecated_env("LAMA_OLE_HOST", "LAMA_OLE_HOST_OLLAMA")
        return legacy
    return DEFAULT_OLLAMA_HOST


def _llamacpp_host_env():
    """Resolve the llama.cpp host from the environment.

    ``LAMA_OLE_HOST_LLAMACPP`` is the canonical variable. The legacy
    ``LAMA_OLE_LLAMACPP_HOST`` is still honored so existing configs keep
    working.
    """
    value = os.environ.get("LAMA_OLE_HOST_LLAMACPP")
    if value:
        return value
    legacy = os.environ.get("LAMA_OLE_LLAMACPP_HOST")
    if legacy:
        _deprecated_env("LAMA_OLE_LLAMACPP_HOST", "LAMA_OLE_HOST_LLAMACPP")
        return legacy
    return DEFAULT_LLAMACPP_HOST


def _llamacpp_api_key_env():
    """Resolve the llama.cpp API key (Bearer token) from the environment.

    ``LAMA_OLE_HOST_LLAMACPP_API_KEY`` is the canonical variable. The legacy
    ``LAMA_OLE_LLAMACPP_API_KEY`` is still honored so existing configs keep
    working.
    """
    value = os.environ.get("LAMA_OLE_HOST_LLAMACPP_API_KEY")
    if value:
        return value
    legacy = os.environ.get("LAMA_OLE_LLAMACPP_API_KEY")
    if legacy:
        _deprecated_env("LAMA_OLE_LLAMACPP_API_KEY", "LAMA_OLE_HOST_LLAMACPP_API_KEY")
        return legacy
    return None


def _env_choice(name, default, choices):
    value = os.environ.get(name)
    if not value:
        return default
    if value in choices:
        return value
    print(f"Warning: ignoring invalid value for {name}: {value!r} "
          f"(must be one of {choices})", file=sys.stderr)
    return default


def _env_list(name):
    value = os.environ.get(name)
    if not value:
        return None
    parts = value.replace(",", " ").split()
    return parts or None


def build_parser():
    parser = argparse.ArgumentParser(
        description="A CLI tool to interact with an Ollama instance.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Define arguments
    parser.add_argument(
        "-V", "--version",
        action="version",
        version="0.0.54"
    )
    # Define arguments
    parser.add_argument(
        "--ollama-host", "--host",
        dest="ollama_host",
        type=str,
        default=_ollama_host_env(),
        help="The host of the Ollama instance (e.g. http://localhost:11434)"
    )
    parser.add_argument(
        "--llamacpp-host",
        dest="llamacpp_host",
        type=str,
        default=_llamacpp_host_env(),
        help="The host of the llama.cpp llama-server (e.g. http://localhost:8080)"
    )
    parser.add_argument(
        "-m", "--model",
        type=str,
        default=_env_str("LAMA_OLE_MODEL", None),
        help="The model name to use (e.g., gemma2:2b)"
    )
    parser.add_argument(
        "-i", "--input",
        type=str,
        help="The input string to send to the model"
    )
    parser.add_argument(
        "-f", "--inputfile",
        type=str,
        help="Path to a file to be used as input"
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="If set, read the input from standard input instead of --input or --inputfile"
    )
    parser.add_argument(
        "-t", "--thinking",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("LAMA_OLE_THINKING", False),
        help="If set, output the model's thought process to the console"
    )
    # parameter for thoughts
    parser.add_argument(
        "--thoughtlog",
        type=str,
        help="Path to a log file where the model's thoughts should be saved (independently of -t)"
    )
    # Added requested parameter: -o or --outlog
    parser.add_argument(
        "-o", "--outlog",
        type=str,
        help="Path to a log file where the main output of the model should be saved"
    )
    # Parameter for tool calls log file (similar to -v but writes to a file)
    parser.add_argument(
        "--toolcalllog",
        type=str,
        help="Path to a log file where tool calls should be logged (similar to -v output)"
    )

    # Parameter for chat input log file
    parser.add_argument(
        "--chatinputlog",
        type=str,
        help="Path to a log file where all user input (stdin, --input/--inputfile, and chat REPL) should be logged with timestamps"
    )
    # Parameter: ndjson conversation log
    parser.add_argument(
        "--logndjson",
        type=str,
        help="Path to a newline-delimited JSON log file where every conversation message is appended as its own line"
    )
    # Parameter: temperature
    parser.add_argument(
        "--temperature",
        type=float,
        default=_env_float("LAMA_OLE_TEMPERATURE", 0.0),
        help="Set the sampling temperature (e.g., 0.7)"
    )

    # Parameter: num_ctx
    parser.add_argument(
        "--num_ctx",
        type=int,
        default=_env_int("LAMA_OLE_NUM_CTX", None),
        help="Set the context window (e.g., 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576)"
    )

    # Parameter: num_gpu
    parser.add_argument(
        "--num_gpu",
        type=int,
        default=_env_int("LAMA_OLE_NUM_GPU", None),
        help="Set the amount of GPU cores"
    )

    # Parameter: keep_alive
    parser.add_argument(
        "--keep_alive",
        type=str,
        default=_env_str("LAMA_OLE_KEEP_ALIVE", None),
        help="Keep model in memory (e.g., '5m', '1h' or a number of seconds)"
    )

    # Parameter: list
    parser.add_argument(
        "-l", "--list",
        action="store_true",
        help="List all available models and exit"
    )

    # Parameter: list
    parser.add_argument(
        "--ps",
        action="store_true",
        help="List all running models and exit"
    )

    # Parameter: stop a loaded model
    parser.add_argument(
        "--stop",
        type=str,
        metavar="MODEL",
        help="Stop/unload a running model (e.g., 'gemma2:2b')"
    )

    # Parameter: ollama websearch
    parser.add_argument(
        "--ollama_websearch",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("LAMA_OLE_OLLAMA_WEBSRCH", False),
        help="Activate Ollama's built-in web search tool"
    )

    # Parameter: verbose (repeatable for levels)
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=_env_int("LAMA_OLE_VERBOSE", 0),
        help="Increase verbosity level (repeat: -v, -vv, -vvv)"
    )

    # Parameter: chat
    parser.add_argument(
        "--chat",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("LAMA_OLE_CHAT", False),
        help="Start an interactive chat REPL session"
    )

    # Parameter: resume
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("LAMA_OLE_RESUME", True),
        help="Automatically resume the most recent session for the current "
             "directory on startup (use --no-resume to always start fresh)"
    )

    # Parameter: autosave
    parser.add_argument(
        "--autosave",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("LAMA_OLE_AUTOSAVE", True),
        help="Automatically save the current chat session to disk after every "
             "turn and on exit (use --no-autosave to stop writing session files)"
    )

    # Parameter: show diff (default on)
    parser.add_argument(
        "--diff",
        action=argparse.BooleanOptionalAction,
        dest="show_diff",
        default=_env_bool("LAMA_OLE_SHOW_DIFF", True),
        help="Show a colored unified diff of each file write (edit/create/"
             "append/apply_patch) in the output (use --no-diff to hide it)"
    )

    # Parameter: color
    parser.add_argument(
        "--color",
        type=str,
        default=_env_choice("LAMA_OLE_COLOR", "auto", ["auto", "always", "never", "none"]),
        choices=["auto", "always", "never", "none"],
        help="Colorize user input, thinking, and LLM output: 'auto' (TTY only), 'always', or 'never'/'none'"
    )

    # Parameter: context window meter
    parser.add_argument(
        "--ctx-meter",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("LAMA_OLE_CTX_METER", True),
        help="Show a context-window usage meter in chat mode (live prompt gauge). "
             "The window size is taken from --num_ctx, LAMA_OLE_CTX_SIZE, or the running model"
    )

    # Parameter: context compaction
    parser.add_argument(
        "--auto-compact",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("LAMA_OLE_AUTO_COMPACT", False),
        help="Enable auto-compaction: when the context window crosses the "
             "threshold, summarize older context (keeping recent turns verbatim)"
    )
    parser.add_argument(
        "--auto-compact-threshold",
        type=sanitize_ctx_threshold,
        default=sanitize_ctx_threshold(
            _env_float("LAMA_OLE_AUTO_COMPACT_THRESHOLD", DEFAULT_CTX_COMPACT_THRESHOLD)
        ),
        help="Fraction of the context window at which auto-compaction triggers "
             "(must be in (0, 1])"
    )
    parser.add_argument(
        "--auto-compact-model",
        type=str,
        default=_env_str("LAMA_OLE_AUTO_COMPACT_MODEL", None),
        help="Model used to produce compaction summaries (falls back to the chat model)"
    )

    # Parameter: safe
    parser.add_argument(
        "--safe",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("LAMA_OLE_SAFE", False),
        help="Enable user confirmation before dangerous tool operations"
    )

    # Parameter: mode
    parser.add_argument(
        "--mode",
        type=str,
        default=_env_choice("LAMA_OLE_MODE", "build", ["build", "plan"]),
        choices=["build", "plan"],
        help="Chat agent mode: 'build' (full tools, changes allowed) or "
             "'plan' (all tools advertised, write tools blocked until /build)"
    )

    # Parameter: tool (repeatable)
    parser.add_argument(
        "--tool",
        type=str,
        action="append",
        dest="tools",
        default=None,
        help="Python module name providing tool functions (can be repeated); "
             "appends to tools configured via LAMA_OLE_TOOL"
    )

    # Parameter: skill (repeatable)
    parser.add_argument(
        "--skill",
        type=str,
        action="append",
        dest="skills",
        default=None,
        help="Path to a skill file whose text is loaded into the system role "
             "(can be repeated; files are concatenated); appends to skills "
             "configured via LAMA_OLE_SKILL"
    )

    # Parameter: ignore-config-tools
    parser.add_argument(
        "--ignore-config-tools",
        action="store_true",
        help="Ignore tools configured via LAMA_OLE_TOOL (shell/env/config) "
             "for this run; only --tool values are used"
    )

    # Parameter: max_tool_rounds
    parser.add_argument(
        "--max_tool_rounds",
        type=int,
        default=_env_int("LAMA_OLE_MAX_TOOL_ROUNDS", None),
        help="Maximum number of tool-calling rounds (no limit when unset)"
    )

    # Parameter: vision_model (repeatable)
    parser.add_argument(
        "--vision_model",
        type=str,
        action="append",
        dest="vision_models",
        default=None,
        help="Vision model name available for media understanding tools (can be repeated)"
    )

    # Parameter: help-tools
    parser.add_argument(
        "--help-tools",
        action="store_true",
        help="Show documentation for loaded tool modules and exit"
    )

    # Parameter: transfer
    parser.add_argument(
        "--transfer",
        nargs=2,
        metavar=("SOURCE", "DEST"),
        help="Transfer a model from SOURCE to DEST ollama instance"
    )

    # Parameter: serve-blobs
    parser.add_argument(
        "--serve-blobs",
        action="store_true",
        help="Start a blob HTTP server for remote transfer"
    )
    parser.add_argument(
        "--blob-host",
        type=str,
        default="127.0.0.1",
        help="Host to bind blob server"
    )
    parser.add_argument(
        "--blob-port",
        type=int,
        default=0,
        help="Port for blob server (0 = random)"
    )

    # Parameter: max_tool_rounds_continuation
    parser.add_argument(
        "--max_tool_rounds_continuation",
        type=str,
        default=_env_choice(
            "LAMA_OLE_MAX_TOOL_ROUNDS_CONTINUATION", "ask", ["ask", "fallback"]
        ),
        choices=["ask", "fallback"],
        help="Behavior when max_tool_rounds is reached: 'ask' (interactive menu) or 'fallback' (silent default)"
    )

    # Parameter: system_prompt
    parser.add_argument(
        "--system_prompt",
        type=str,
        default=_env_str("LAMA_OLE_SYSTEM_PROMPT", None),
        help="The system prompt"
    )

    # Parameter: system_prompt_file
    parser.add_argument(
        "--system_prompt_file",
        type=str,
        default=_env_str("LAMA_OLE_SYSTEM_PROMPT_FILE", None),
        help="The system prompt read from a file"
    )

    # Parameter: no_safety_system_prompt
    parser.add_argument(
        "--no_safety_system_prompt",
        action="store_true",
        help="Enables potential takeover when tools are used, it is placed after the system prompt, if given"
    )

    # Parameter: debug
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Initialize the environment and enter interactive mode"
    )

    return parser


def _merge_tool_lists(env_tools, cli_tools):
    """Combine env/config defaults with CLI tools, deduped, env-first.

    First occurrence wins so a module listed both in config and on the CLI is
    loaded once (load_tools() appends module info on every call).
    """
    merged = []
    for item in (env_tools or []) + (cli_tools or []):
        if item not in merged:
            merged.append(item)
    return merged or None


def _load_skill_text(skill_paths):
    """Load one or more skill files, entropy-check each, and concatenate.

    Each skill file must pass the entropy check (reject binary/random data).
    Returns the concatenated skill text with a blank line between files.
    Exits with an error message on missing/rejected/invalid files.
    """
    from security.entropychecker import EntropyChecker

    parts = []
    for path in skill_paths:
        if not os.path.exists(path):
            print(f"Error: The skill file '{path}' was not found.", file=sys.stderr)
            sys.exit(1)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception as e:
            print(f"Error reading skill file '{path}': {e}", file=sys.stderr)
            sys.exit(1)

        checker = EntropyChecker()
        result = checker.feed(raw)
        if result.is_suspicious:
            print(
                f"Error: skill file '{path}' rejected by entropy check: {result.reason}",
                file=sys.stderr,
            )
            sys.exit(1)

        content = raw.decode("utf-8", errors="replace")
        parts.append(content)

    return "\n\n".join(parts)


def _resolve_env_defaults(args):
    # --tool: merge env/config defaults with CLI values (unless ignored).
    # --vision_model: strict override (CLI replaces env/config defaults).
    if not args.ignore_config_tools:
        args.tools = _merge_tool_lists(_env_list("LAMA_OLE_TOOL"), args.tools)
    args.skills = _merge_tool_lists(_env_list("LAMA_OLE_SKILL"), args.skills)
    if args.vision_models is None:
        args.vision_models = _env_list("LAMA_OLE_VISION_MODEL")


def _default_sessions_dir():
    """XDG-aware sessions directory (~/.local/share/lama_ole/sessions)."""
    base = os.environ.get("LAMA_OLE_SESSION_DIR")
    if not base:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            base = os.path.join(xdg, "lama_ole")
        else:
            base = os.path.join(os.path.expanduser("~"), ".local", "share", "lama_ole")
        base = os.path.join(base, "sessions")
    return base


    """True when two model ids refer to the same backend model.

    Bare names and their namespaced form compare equal
    (``gemma2:2b`` == ``ollama.gemma2:2b``) so resuming an older session does
    not spuriously prompt for a model choice.
    """
    if a is None or b is None:
        return a == b
    canonicalize = getattr(client, "canonicalize", None)
    if callable(canonicalize):
        return canonicalize(a) == canonicalize(b)
    return a == b


def _resume_session_into(state, resume):
    """Restore the most recent session into ``state`` at startup.

    If the CLI model differs from the session's model, ask the user which to
    keep (the session model, the CLI model, or abort). Canonically identical
    models (e.g. a bare name vs its namespaced form) are adopted silently.
    ``args.model`` is not updated for the resumed turns; the chosen model is
    applied to the state.
    """
    path, data = resume
    session_model = data.get("model")
    cli_model = state.model
    if cli_model and session_model:
        if _same_model(cli_model, session_model, state.client):
            data["model"] = cli_model
        else:
            # Session model always wins
            data["model"] = session_model
    elif cli_model:
        data["model"] = cli_model
    apply_session(state, data, source=path)
    title = data.get("title") or "(untitled)"
    n = len(state.messages)
    print(
        f"Resumed session '{title}' ({n} messages). Use --no-resume to start fresh.",
        file=sys.stderr,
    )
    _replay_history(state, color_util.color_mode_enabled(state.color))


def main():
    load_env_files()
    args = build_parser().parse_args()
    _resolve_env_defaults(args)

    color_util.configure(
        prompt=_env_str("LAMA_OLE_COLOR_PROMPT", None),
        thinking=_env_str("LAMA_OLE_COLOR_THINKING", None),
        output=_env_str("LAMA_OLE_COLOR_OUTPUT", None),
        input=_env_str("LAMA_OLE_COLOR_INPUT", None),
        meter_low=_env_str("LAMA_OLE_COLOR_METER_LOW", None),
        meter_mid=_env_str("LAMA_OLE_COLOR_METER_MID", None),
        meter_high=_env_str("LAMA_OLE_COLOR_METER_HIGH", None),
    )

    ollama_host = _normalize_host_with_default(args.ollama_host, 11434)
    llamacpp_host = _normalize_host_with_default(args.llamacpp_host, 8080)
    client = create_router(
        ollama_host=ollama_host,
        llamacpp_host=llamacpp_host,
        api_key=_llamacpp_api_key_env(),
    )

    # Propagate host and vision models to tools
    set_ollama_host(ollama_host)
    if args.vision_models:
        set_vision_models(args.vision_models)

    if args.serve_blobs:
        from tools.blob_server import run_server
        run_server(host=args.blob_host, port=args.blob_port)
        sys.exit(0)

    if args.list:
        print("available models:")
        response = client.list()
        for entry in response.models:
            print(entry.model)

    if args.ps:
        print("running models:")
        response = client.ps()
        for entry in response.models:
            print(entry.model)


    if args.list or args.ps:
     sys.exit(0)

    if args.stop:
        stopped = client.stop(args.stop)
        if stopped:
            print(f"Stopped model: {args.stop}")
        else:
            print(
                f"Model {args.stop} is managed by the llama.cpp server; "
                "nothing to unload.",
                file=sys.stderr,
            )
        sys.exit(0)

    if args.transfer:
        src_raw, dst_host = args.transfer
        dst_host = _normalize_host(dst_host)
        if not args.model:
            print("Error: --model is required for --transfer", file=sys.stderr)
            sys.exit(1)
        client_dst = Client(host=dst_host)
        if src_raw == "localhost" or src_raw == "localhost:11434":
            source = FilesystemBlobSource()
            client_src = Client(host=_normalize_host("localhost"))
            show = client_src.show(model=args.model)
            create_kwargs = _parse_modelfile(show.modelfile or "")
            _transfer_model(client_dst, args.model, source, create_kwargs)
        elif src_raw.startswith("http://") or src_raw.startswith("https://"):
            blob_url = src_raw.rstrip("/")
            source = HttpBlobSource(blob_url)
            config = source.get_config(args.model)
            create_kwargs = _config_to_create_kwargs(config)
            _transfer_model(client_dst, args.model, source, create_kwargs)
        else:
            print("Error: source must be 'localhost' or a blob server URL "
                  "(http://...)", file=sys.stderr)
            sys.exit(1)
        source.cleanup()
        sys.exit(0)

    # Load tools if --tool was specified (needed early for --help-tools)
    loaded_tools = []
    if args.tools:
        for module_name in args.tools:
            try:
                module_tools = load_tools(module_name)
                loaded_tools.extend(module_tools)
            except Exception as e:
                print(f"Error loading tool module '{module_name}': {e}", file=sys.stderr)
                sys.exit(1)
    ollama_tools = to_openai_tools(loaded_tools) if loaded_tools else None

    if args.debug:
        import code
        print(
            f"Debug mode: model={args.model}, "
            f"ollama={ollama_host}, llamacpp={llamacpp_host}"
        )
        local_vars = {
            'client': client,
            'loaded_tools': loaded_tools,
            'ollama_tools': ollama_tools,
            'args': args,
            'ollama_host': ollama_host,
            'llamacpp_host': llamacpp_host,
            'sys': sys,
            'os': os,
        }
        code.interact(local=locals())
        sys.exit(0)

    # Handle --help-tools
    if args.help_tools:
        modules = get_tool_modules_info()
        if not modules:
            print("No tool modules loaded. Use --tool to specify modules.", file=sys.stderr)
            sys.exit(1)
        for mod in modules:
            print(f"Tool Module: {mod.module_name}")
            if mod.env_vars:
                print("  Environment Variables:")
                for var, desc in mod.env_vars.items():
                    print(f"    {var}: {desc}")
            print("  Functions:")
            for t in mod.tools:
                sig_parts = []
                for pname, pinfo in t.parameters.get("properties", {}).items():
                    ptype = pinfo.get("type", "string")
                    if pname in t.parameters.get("required", []):
                        sig_parts.append(f"{pname}: {ptype}")
                    else:
                        sig_parts.append(f"[{pname}: {ptype}]")
                sig = ", ".join(sig_parts)
                print(f"    {t.name}({sig}) — {t.description}")
        sys.exit(0)

    if not args.model:
        default_model = client.resolve_default_model()
        if default_model:
            args.model = default_model
            print(f"Using llama.cpp model: {args.model}", file=sys.stderr)
        else:
            try:
                resp = client.list()
                candidates = [entry.model for entry in resp.models]
            except Exception:
                candidates = []
            if candidates:
                print(
                    "Error: model has to be set (parameter -m, --model). "
                    "Available models: " + ", ".join(candidates),
                    file=sys.stderr,
                )
            else:
                print(
                    "Error: model has to be set (parameter -m, --model) and no "
                    "reachable backend is serving models.",
                    file=sys.stderr,
                )
            sys.exit(1)

    args.model = client.canonicalize(args.model)

    # Determine initial content (optional in chat mode)
    content = ""
    if args.input:
        content = args.input
    elif args.inputfile:
        if os.path.exists(args.inputfile):
            with open(args.inputfile, 'r', encoding='utf-8') as f:
                content = f.read()
        else:
            print(f"Error: The file '{args.inputfile}' was not found.", file=sys.stderr)
            sys.exit(1)
    elif args.stdin:
        content = sys.stdin.read()

    if not args.chat and not content.strip():
        print("Error: You must provide content via -i, --inputfile, --stdin or use --chat", file=sys.stderr)
        sys.exit(1)

    system_prompt = None
    if args.system_prompt :
        system_prompt = args.system_prompt
    elif args.system_prompt_file :
        if os.path.exists(args.system_prompt_file):
            with open(args.system_prompt_file, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        else:
            print(f"Error: The file '{args.system_prompt_file}' was not found.", file=sys.stderr)
            sys.exit(1)

    skill_text = None
    if args.skills:
        skill_text = _load_skill_text(args.skills)


    # File handles
    thought_file_handle = None
    output_file_handle = None

    # Open the thought log if provided
    if args.thoughtlog:
        if os.path.exists(args.thoughtlog):
            print(f"Error: The file '{args.thoughtlog}' already exists.", file=sys.stderr)
            sys.exit(1)
        thought_file_handle = open(args.thoughtlog, "w", encoding="utf-8")

    # Open the output log if provided
    if args.outlog:
        if os.path.exists(args.outlog):
            print(f"Error: The file '{args.outlog}' already exists.", file=sys.stderr)
            sys.exit(1)
        output_file_handle = open(args.outlog, "w", encoding="utf-8")

    # Open the toolcall log if provided
    toolcall_file_handle = None
    if args.toolcalllog:
        if os.path.exists(args.toolcalllog):
            print(f"Error: The file '{args.toolcalllog}' already exists.", file=sys.stderr)
            sys.exit(1)
        toolcall_file_handle = open(args.toolcalllog, "w", encoding="utf-8")

    # Open the chat input log if provided
    chatinput_file_handle = None
    if args.chatinputlog:
        if os.path.exists(args.chatinputlog):
            print(f"Error: The file '{args.chatinputlog}' already exists.", file=sys.stderr)
            sys.exit(1)
        chatinput_file_handle = open(args.chatinputlog, "w", encoding="utf-8")

    # Open the ndjson conversation log if provided
    ndjson_log_file_handle = None
    if args.logndjson:
        if os.path.exists(args.logndjson):
            print(f"Error: The file '{args.logndjson}' already exists.", file=sys.stderr)
            sys.exit(1)
        ndjson_log_file_handle = open(args.logndjson, "w", encoding="utf-8")

    try:
        options = {
            "temperature": args.temperature,
        }

        if None != args.num_ctx:
            options["num_ctx"] = args.num_ctx

        if None != args.num_gpu:
            options["num_gpu"] = args.num_gpu

        if args.chat:
            sessions_dir = _default_sessions_dir()
            state = ChatState(
                client=client,
                model=args.model,
                loaded_tools=loaded_tools,
                loaded_tool_modules=list(args.tools or []),
                ollama_tools=ollama_tools,
                options=options,
                keep_alive=args.keep_alive,
                show_thinking=args.thinking,
                no_safety_system_prompt=args.no_safety_system_prompt,
                system_prompt= system_prompt,
                skill_text= skill_text,
                verbose=args.verbose,
                safe=args.safe,
                mode=args.mode,
                show_diff=args.show_diff,
                thought_file_handle=thought_file_handle,
                output_file_handle=output_file_handle,
                toolcall_file_handle=toolcall_file_handle,
                chatinput_file_handle=chatinput_file_handle,
                max_tool_rounds=args.max_tool_rounds,
                max_tool_rounds_continuation=args.max_tool_rounds_continuation,
                ollama_websearch=args.ollama_websearch,
                ndjson_log_path=args.logndjson,
                ndjson_log_file_handle=ndjson_log_file_handle,
                color=args.color,
                sessions_dir=sessions_dir,
                session_autosave=args.autosave,
                ctx_meter=args.ctx_meter,
                ctx_max=_env_int("LAMA_OLE_CTX_SIZE", None),
                ctx_compact=args.auto_compact,
                ctx_compact_threshold=args.auto_compact_threshold,
                ctx_compact_model=args.auto_compact_model,
            )
            if args.resume:
                resume = find_recent_session(sessions_dir, os.getcwd())
                if resume:
                    _resume_session_into(state, resume)
            if not state.session_id:
                state.session_id = new_session_id()
                state.session_created_at = time.time()
            if content.strip():
                user_msg = {"role": "user", "content": content}
                state.stamp_message(user_msg)
                state.messages.append(user_msg)
                state.log_ndjson(user_msg)
                try:
                    metrics = {}
                    run_with_tools(
                        client=client,
                        model=args.model,
                        messages=state.messages,
                        loaded_tools=loaded_tools,
                        ollama_tools=ollama_tools,
                        options=options,
                        keep_alive=args.keep_alive,
                        show_thinking=args.thinking,
                        no_safety_system_prompt= args.no_safety_system_prompt,
                        system_prompt= system_prompt,
                        skill_text= skill_text,
                        verbose=args.verbose,
                        safe=args.safe,
                        mode=args.mode,
                        show_diff=args.show_diff,
                        thought_file_handle=thought_file_handle,
                        output_file_handle=output_file_handle,
                        toolcall_file_handle=toolcall_file_handle,
                        chatinput_file_handle=chatinput_file_handle,
                        max_tool_rounds=args.max_tool_rounds,
                        max_tool_rounds_continuation=args.max_tool_rounds_continuation,
                        ollama_websearch=args.ollama_websearch,
                        ndjson_log_file_handle=ndjson_log_file_handle,
                        color=args.color,
                        state_manager=state.state_manager,
                        metrics=metrics,
                    )
                    state.ctx_usage = metrics
                    state.ctx_usage_model = args.model
                    autosave_session(state)
                except KeyboardInterrupt:
                    print(
                        "\nInterrupted during initial response. Entering chat mode.",
                        file=sys.stderr,
                    )
                    state.state_manager.reset()
                    # Partial rollback: keep the user message (and the system
                    # prompt that run_with_tools prepended for this turn) plus
                    # any completed tool rounds; drop only a trailing tool call
                    # that never got its result.
                    _drop_incomplete_trailing_messages(state, user_msg)
            run_chat(state)
        else:
            from tool_base.logging import _log_ndjson_message

            messages = [{"role": "user", "content": content}]
            if ndjson_log_file_handle:
                _log_ndjson_message(ndjson_log_file_handle, args.model, messages[0])
            run_with_tools(
                client=client,
                model=args.model,
                messages=messages,
                loaded_tools=loaded_tools,
                ollama_tools=ollama_tools,
                options=options,
                keep_alive=args.keep_alive,
                show_thinking=args.thinking,
                no_safety_system_prompt= args.no_safety_system_prompt,
                system_prompt= system_prompt,
                skill_text= skill_text,
                verbose=args.verbose,
                safe=args.safe,
                mode=args.mode,
                show_diff=args.show_diff,
                thought_file_handle=thought_file_handle,
                output_file_handle=output_file_handle,
                toolcall_file_handle=toolcall_file_handle,
                chatinput_file_handle=chatinput_file_handle,
                max_tool_rounds=args.max_tool_rounds,
                max_tool_rounds_continuation=args.max_tool_rounds_continuation,
                ollama_websearch=args.ollama_websearch,
                ndjson_log_file_handle=ndjson_log_file_handle,
                color=args.color,
            )

    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"An error occurred: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if thought_file_handle:
            thought_file_handle.close()
        if output_file_handle:
            output_file_handle.close()
        if toolcall_file_handle:
            toolcall_file_handle.close()
        if chatinput_file_handle:
            chatinput_file_handle.close()
        if ndjson_log_file_handle:
            ndjson_log_file_handle.close()
        if args.chat and args.autosave and "state" in locals():
            try:
                autosave_session(state)
            except Exception as e:
                print(f"Error saving session on exit: {e}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Transfer implementation
# ---------------------------------------------------------------------------


def _normalize_host_with_default(host, default_port):
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    if ":" not in host.split("/")[-1]:
        host = f"{host}:{default_port}"
    return host


def _normalize_host(host):
    return _normalize_host_with_default(host, 11434)


def _find_models_dir():
    env = os.environ.get("OLLAMA_MODELS")
    if env and os.path.isdir(os.path.join(env, "blobs")):
        return env
    candidates = [
        os.path.expanduser("~/.ollama/models"),
        "/usr/share/ollama/.ollama/models",
        "/var/snap/ollama/common/models",
    ]
    for path in candidates:
        if os.path.isdir(os.path.join(path, "blobs")):
            return path
    return os.path.expanduser("~/.ollama/models")


def _parse_model_name(model):
    if ":" in model:
        name, tag = model.split(":", 1)
    else:
        name, tag = model, "latest"
    return name, tag


def _manifest_path(models_dir, model):
    name, tag = _parse_model_name(model)
    if "/" in name:
        parts = ["manifests", "registry.ollama.ai"] + name.split("/")
    else:
        parts = ["manifests", "registry.ollama.ai", "library", name]
    return os.path.join(models_dir, *parts, tag)


def _read_manifest(models_dir, model):
    path = _manifest_path(models_dir, model)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _find_gguf_digest(manifest):
    for layer in manifest.get("layers", []):
        if layer.get("mediaType") == "application/vnd.ollama.image.model":
            return layer["digest"]
    layers = manifest.get("layers", [])
    if layers:
        return max(layers, key=lambda l: l.get("size", 0))["digest"]
    return None


def _upload_blobs(client_dst, blob_source, manifest):
    digests = [l["digest"] for l in manifest.get("layers", [])]
    config = manifest.get("config")
    if config:
        digests.append(config["digest"])
    for digest in digests:
        print(f"  Uploading blob {digest[:20]}...", file=sys.stderr, end=" ")
        try:
            blob_path = blob_source.get_blob_path(digest)
            client_dst.create_blob(Path(blob_path))
            print("OK", file=sys.stderr)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            raise


def _parse_param_value(val):
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] == '"':
        return val[1:-1]
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _is_model_name(value):
    if value.startswith(("/", "./", "../", "~", "@")):
        return False
    if "\\" in value:
        return False
    if value.lower().endswith((".gguf", ".bin", ".safetensors")):
        return False
    return True

def _parse_modelfile(modelfile):
    kwargs = {}
    params = {}
    text = re.sub(r"(?m)^\s*#.*$", "", modelfile)

    m = re.search(r"^FROM\s+(.+)$", text, re.MULTILINE)
    if m:
        from_val = m.group(1).strip()
        if _is_model_name(from_val):
            kwargs["from_"] = from_val

    for directive in ("TEMPLATE", "SYSTEM", "LICENSE"):
        m = re.search(
            rf'^{directive}\s+"""(.*?)"""\s*$',
            text, re.MULTILINE | re.DOTALL,
        )
        if m:
            kwargs[directive.lower()] = m.group(1).strip()

    for m in re.finditer(r"^PARAMETER\s+(\S+)\s+(.+)$", text, re.MULTILINE):
        key = m.group(1)
        val = m.group(2).strip()
        if key == "stop":
            params.setdefault("stop", []).append(_parse_param_value(val))
        else:
            params[key] = _parse_param_value(val)

    if params:
        kwargs["parameters"] = params
    return kwargs



def _config_to_create_kwargs(config):
    kwargs = {}
    params = {}
    for key, val in config.items():
        if key == "template":
            kwargs["template"] = val
        elif key == "system":
            kwargs["system"] = val
        elif key == "license":
            kwargs["license"] = val
        elif key == "stop" and isinstance(val, list):
            params["stop"] = val
        elif not isinstance(val, (list, dict)):
            params[key] = val
    if params:
        kwargs["parameters"] = params
    return kwargs


class BlobSource:
    def get_manifest(self, model_name):
        raise NotImplementedError

    def get_blob_path(self, digest):
        raise NotImplementedError

    def cleanup(self):
        pass


class FilesystemBlobSource(BlobSource):
    def __init__(self, models_dir=None):
        self.models_dir = models_dir or _find_models_dir()

    def get_manifest(self, model_name):
        return _read_manifest(self.models_dir, model_name)

    def get_blob_path(self, digest):
        safe = digest.replace(":", "-")
        return os.path.join(self.models_dir, "blobs", safe)


class HttpBlobSource(BlobSource):
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self._temp_dir = tempfile.mkdtemp(prefix="lama_ole_blobs_")

    def get_manifest(self, model_name):
        name, tag = _parse_model_name(model_name)
        url = f"{self.base_url}/manifest/{name}/{tag}"
        with urllib.request.urlopen(url) as resp:
            return json.loads(resp.read().decode())

    def get_blob_path(self, digest):
        safe = digest.replace(":", "-")
        local_path = os.path.join(self._temp_dir, safe)
        if os.path.exists(local_path):
            return local_path
        url = f"{self.base_url}/blobs/{safe}"
        with urllib.request.urlopen(url) as resp, \
             open(local_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        return local_path

    def get_config(self, model_name):
        name, tag = _parse_model_name(model_name)
        url = f"{self.base_url}/show/{name}/{tag}"
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read().decode())
        return data.get("config", {})

    def cleanup(self):
        shutil.rmtree(self._temp_dir, ignore_errors=True)


def _transfer_model(client_dst, model, blob_source, create_kwargs):
    print(f"Transferring '{model}' ...", file=sys.stderr)

    manifest = blob_source.get_manifest(model)
    if not manifest:
        print("Error: manifest not found", file=sys.stderr)
        sys.exit(1)

    _upload_blobs(client_dst, blob_source, manifest)

    gguf_digest = _find_gguf_digest(manifest)
    if gguf_digest:
        create_kwargs.setdefault("files", {})["model.gguf"] = gguf_digest

    print("  Creating model ...", file=sys.stderr, end=" ")
    client_dst.create(model=model, stream=False, **create_kwargs)
    print("OK", file=sys.stderr)
    print(f"Model '{model}' transferred successfully.", file=sys.stderr)


if __name__ == "__main__":
    main()
