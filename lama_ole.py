#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
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
    to_ollama_tools,
    run_with_tools,
)
from chat import ChatState, run_chat

def main():
    parser = argparse.ArgumentParser(
        description="A CLI tool to interact with an Ollama instance."
    )
    # Define arguments
    parser.add_argument(
        "-V", "--version",
        action="version",
        version="0.0.37"
    )
    # Define arguments
    parser.add_argument(
        "--host",
        type=str,
        default="http://localhost:11434",
        help="The host of the ollama instance (e.g., localhost:11434)"
    )
    parser.add_argument(
        "-m", "--model",
        type=str,
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
        action="store_true",
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
        default=0.0,
        help="Set the sampling temperature (e.g., 0.7). Default is 0.0"
    )

    # Parameter: num_ctx
    parser.add_argument(
        "--num_ctx",
        type=int,
        default=None,
        help="Set the context window (e.g., 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576)"
    )

    # Parameter: num_gpu
    parser.add_argument(
        "--num_gpu",
        type=int,
        default=None,
        help="Set the amount of GPU cores"
    )

    # Parameter: keep_alive
    parser.add_argument(
        "--keep_alive",
        type=str,
        default=None,
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
        action="store_true",
        help="Activate Ollama's built-in web search tool"
    )

    # Parameter: verbose (repeatable for levels)
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity level (repeat: -v, -vv, -vvv)"
    )

    # Parameter: chat
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Start an interactive chat REPL session"
    )

    # Parameter: color
    parser.add_argument(
        "--color",
        type=str,
        default="auto",
        choices=["auto", "always", "never"],
        help="Colorize user input, thinking, and LLM output: 'auto' (TTY only), 'always', or 'never' (default: auto)"
    )

    # Parameter: safe
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Enable user confirmation before dangerous tool operations"
    )

    # Parameter: tool (repeatable)
    parser.add_argument(
        "--tool",
        type=str,
        action="append",
        dest="tools",
        default=None,
        help="Python module name providing tool functions (can be repeated)"
    )

    # Parameter: max_tool_rounds
    parser.add_argument(
        "--max_tool_rounds",
        type=int,
        default=None,
        help="Maximum number of tool-calling rounds (default: no limit)"
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
        help="Host to bind blob server (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--blob-port",
        type=int,
        default=0,
        help="Port for blob server (default: random)"
    )

    # Parameter: max_tool_rounds_continuation
    parser.add_argument(
        "--max_tool_rounds_continuation",
        type=str,
        default="ask",
        choices=["ask", "fallback"],
        help="Behavior when max_tool_rounds is reached: 'ask' (interactive menu) or 'fallback' (silent default)"
    )

    # Parameter: system_prompt
    parser.add_argument(
        "--system_prompt",
        type=str,
        help="The system prompt"
    )

    # Parameter: system_prompt_file
    parser.add_argument(
        "--system_prompt_file",
        type=str,
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

    args = parser.parse_args()

    host_url = args.host
    if not host_url.startswith(('http://', 'https://')) and ':' in host_url:
        host_url = f"http://{host_url}"
    client = Client(host=host_url)

    # Propagate host and vision models to tools
    set_ollama_host(host_url)
    if args.vision_models:
        set_vision_models(args.vision_models)

    if args.serve_blobs:
        from tools.blob_server import run_server
        run_server(host=args.blob_host, port=args.blob_port)
        sys.exit(0)

    if args.list:
        print( "available models:")
        response = client.list()
        for model in response.models:
            print(model)

    if args.ps:
        print( "running models:")
        response = client.ps()
        for model in response.models:
            print(model)


    if args.list or args.ps:
     sys.exit(0)

    if args.stop:
        client.generate(model=args.stop, keep_alive=0)
        print(f"Stopped model: {args.stop}")
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
    ollama_tools = to_ollama_tools(loaded_tools) if loaded_tools else None

    if args.debug:
        import code
        print(f"Debug mode: model={args.model}, host={host_url}")
        local_vars = {
            'client': client,
            'loaded_tools': loaded_tools,
            'ollama_tools': ollama_tools,
            'args': args,
            'host_url': host_url,
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

    if not args.model :
        print( "Error: model has to be set (parameter -m , --model)", file=sys.stderr)
        sys.exit(1)

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
            state = ChatState(
                client=client,
                model=args.model,
                loaded_tools=loaded_tools,
                ollama_tools=ollama_tools,
                options=options,
                keep_alive=args.keep_alive,
                show_thinking=args.thinking,
                no_safety_system_prompt=args.no_safety_system_prompt,
                system_prompt= system_prompt,
                verbose=args.verbose,
                safe=args.safe,
                thought_file_handle=thought_file_handle,
                output_file_handle=output_file_handle,
                toolcall_file_handle=toolcall_file_handle,
                chatinput_file_handle=chatinput_file_handle,
                max_tool_rounds=args.max_tool_rounds,
                max_tool_rounds_continuation=args.max_tool_rounds_continuation,
                ollama_websearch=args.ollama_websearch,
                ndjson_log_path=args.logndjson,
                ndjson_log_file_handle=ndjson_log_file_handle,
            )
            if content.strip():
                user_msg = {"role": "user", "content": content}
                state.messages.append(user_msg)
                state.log_ndjson(user_msg)
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
                    verbose=args.verbose,
                    safe=args.safe,
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
                verbose=args.verbose,
                safe=args.safe,
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

# ---------------------------------------------------------------------------
# Transfer implementation
# ---------------------------------------------------------------------------


def _normalize_host(host):
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    if ":" not in host.split("/")[-1]:
        host = f"{host}:11434"
    return host


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
