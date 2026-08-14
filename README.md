***

# lama_ole

A CLI tool to interact with **Ollama** and **llama.cpp** (`llama-server`)
instances. Supports streaming chat, tool calling, thinking-process handling,
media understanding (image/video/audio), and flexible input/output options.

## For the impatient developer
```
lama_ole.py --host localhost -m gemma4:26b-a4b-it-qat --chat -t -v --tool tools.dev_tools_readonly --tool tools.edit --logndjson log.ndjson
```

## Features

- **Streaming Support** — Real-time output as the model generates text.
- **Thinking Process** — Display or save the model's internal thought process
  (`-t`, `--thoughtlog`).
- **Output Redirection** — Save generated content to a log file (`-o`, `--outlog`).
- **Tool Call Logging** — Log tool calls and results to a separate file (`--toolcalllog`).
- **Chat Input Logging** — Log chat REPL input to a separate file (`--chatinputlog`).
- **NDJSON Logging** — Log every conversation message (timestamp, model,
  message) as its own NDJSON line (`--logndjson`).
- **Flexible Input** — Direct string (`-i`), file (`-f`), or stdin (`--stdin`).
- **Chat Mode** — Multi-turn REPL with slash commands (`--chat`).
- **Plan / Build Modes** — Switch between a read-only *plan* mode (write tools
  blocked, still advertised) and the full *build* mode with **Shift+Tab**,
  `/plan`, or `/build`.
- **History Editing** — Inspect and surgically edit the conversation history
  with `/history` and `/cut` (including undo); Ctrl-C only discards the
  incomplete part of the interrupted turn.
- **Tool Calling** — Load Python modules as callable tools (`--tool`).
- **Tool Documentation** — Inspect loaded tools, their signatures, and
  environment variables (`--help-tools`).
- **Media Understanding** — Image description/OCR, video frame analysis, audio
  transcription via bundled `tools.media_understanding_tools`.
- **Model Listing** — List available or running models (`-l`, `--ps`).
- **Dual Backends** — Use Ollama and a llama.cpp `llama-server` side by side;
  the backend is selected per model by its `ollama.` / `llamacpp.` name
  namespace (`--ollama-host` / `--llamacpp-host`).
- **Ollama Options** — Pass through `temperature`, `num_ctx`, `num_gpu`,
  `keep_alive`.
- **Model Transfer** — Copy models between ollama instances (`--transfer`).
  Supports localhost-to-remote and remote-to-remote via a built-in blob HTTP
  server (`--serve-blobs`).

## Prerequisites

1. **Ollama** installed and running ([ollama.com](https://ollama.com)).
2. **Python 3.9+**.
3. The `ollama` Python library.

### Installation

```bash
pip install ollama
```

## Getting Started

### Basic Usage

```bash
python3 lama_ole.py -m gemma2:2b -i "Explain the theory of relativity."
```

### Thinking Feature

```bash
python3 lama_ole.py -m gemma2:2b -t -i "Solve a complex math problem step by step."
```

### Saving Outputs

```bash
python3 lama_ole.py -m gemma2:2b -i "Write a story" \
    --thoughtlog thoughts.txt -o story.txt
```

### Piping Input

```bash
echo "Tell me a joke." | python3 lama_ole.py --stdin -m llama3.2:3b
```

### Chat Mode

```bash
python3 lama_ole.py --chat -m llama3.2:3b
```

With an initial system message:

```bash
python3 lama_ole.py --chat -m llama3.2:3b -i "You are a helpful assistant."
```

## llama.cpp Backend

lama_ole can also talk to a **llama.cpp** `llama-server` (the OpenAI-compatible
HTTP server that ships with llama.cpp) in parallel with Ollama. The backend is
selected per model via a name namespace, so a single session can switch between
the two with `/model`.

Start the server (tool calling requires the `--jinja` flag):

```bash
llama-server -m /models/qwen2.5-7b-instruct-q4_k_m.gguf --jinja --port 8080
```

Use a llama.cpp model by prefixing its served id with `llamacpp.`:

```bash
python3 lama_ole.py -m llamacpp.qwen2.5-7b-instruct-q4_k_m.gguf -i "Hello!"
```

### Hosts

- `--ollama-host` / `--host` (env `LAMA_OLE_HOST_OLLAMA`, default
  `http://localhost:11434`) — the Ollama server. The legacy name
  `LAMA_OLE_HOST` is still honored with a deprecation warning.
- `--llamacpp-host` (env `LAMA_OLE_HOST_LLAMACPP`, default
  `http://localhost:8080`) — the llama.cpp `llama-server`. The legacy name
  `LAMA_OLE_LLAMACPP_HOST` is still honored with a deprecation warning.
- `LAMA_OLE_HOST_LLAMACPP_API_KEY` — sent as a `Bearer` token if the server is
  configured with an API key (legacy `LAMA_OLE_LLAMACPP_API_KEY` still honored).
- `--llamacpp-autostart` / `--no-llamacpp-autostart` (env
  `LAMA_OLE_LLAMACPP_AUTOSTART`, default **on**) — start a `llama-server` when
  none is running; only for a **local** host (a remote llama.cpp server is
  never shadowed). See "Supported features" for what it serves and how it is
  configured (`LAMA_OLE_LLAMACPP_BIN`, `LAMA_OLE_LLAMACPP_MODELS_DIR`,
  `LAMA_OLE_LLAMACPP_ARGS`, `LAMA_OLE_LLAMACPP_STOP_ON_EXIT`).

Both hosts can be set in the env file
(`~/.config/lama_ole/lama_ole.env` or `./lama_ole.env`).

### Model naming

The namespace is everything before the *first* dot; when it is not a known
backend name, the model belongs to Ollama.

| Model id | Backend |
|----------|---------|
| `gemma2:2b` | Ollama |
| `ollama.gemma2:2b` | Ollama |
| `llamacpp.qwen2.5-7b-q4_k_m.gguf` | llama.cpp |
| `/models/qwen.gguf` | llama.cpp (basename becomes the id) |

`/model` completion lists both backends together (`ollama.…` and
`llamacpp.…`). When `-m` is omitted, lama_ole defaults to the first llama.cpp
model if Ollama is unreachable but the llama-server is serving models.

### Supported features

Streaming, tool calling, thinking display (`-t`, `--thoughtlog`), the context
meter, `/stats`, compaction, and `/model` switching all work against both
backends. llama.cpp specifics:

- When no llama-server answers at the llama.cpp host, lama_ole **auto-starts
  one** (default on; `--no-llamacpp-autostart` or
  `LAMA_OLE_LLAMACPP_AUTOSTART=false` disables it). This only engages for a
  **local** host (`localhost`, `127.0.0.1`, `::1`) — a remote llama.cpp server
  is never shadowed by a local spawn. The server is always started in
  **router mode**, serving the llama.cpp cache (`$LLAMA_CACHE` or
  `~/.cache/llama.cpp`) and `LAMA_OLE_LLAMACPP_MODELS_DIR`, so `/model`
  completion and mid-chat switching see every cached model. An
  `owner/name[:tag]` Hugging Face id is served only once the server lists it
  (checked against `/v1/models`, not a local path guess); if a targeted HF
  model is missing, a stderr notice explains the one-time download (e.g.
  `llama-server --hf-repo owner/name:quant`). Binary:
  `LAMA_OLE_LLAMACPP_BIN` or `llama-server` on PATH; extra args via
  `LAMA_OLE_LLAMACPP_ARGS`. The launched server stays running after exit so
  later runs reuse it (set `LAMA_OLE_LLAMACPP_STOP_ON_EXIT=true` to kill it).
- When lama_ole starts the server, `--num_ctx`, `--num_gpu`, and `--keep_alive`
  are honored at launch (`-c`, `-ngl`, `--sleep-idle-seconds`), and later runs
  recognize the autostarted daemon from a state marker so those options stay
  quiet. Against a server it did not start, or when the request differs from
  the launch-time values, they are ignored with a one-time actionable warning
  (the `num_ctx` warning reports the server's actual window from `/props`).
- Per-request sampling options (`--temperature`, `--top_p`, `--top_k`,
  `--repeat_penalty`, `--presence_penalty`, `--frequency_penalty`, `--min_p`,
  `--seed`) are forwarded to the server; `num_ctx`/`num_gpu` are never sent
  per request.
- `--ollama_websearch` is unavailable and skipped with a warning.
- Media-understanding tools and `--transfer` remain Ollama-only.

## Tool Calling

### Basic Tool Usage

Load one or more tool modules. The LLM can then invoke them automatically.

```bash
python3 lama_ole.py -m llama3.2:3b -i "What's the weather in Paris?" \
    --tool tools.example_tools
```

### Chat Mode with Tools

```bash
python3 lama_ole.py --chat -m llama3.2:3b --tool tools.example_tools
```

### Seeing What Was Edited

Every file write made by a tool (edit, create_new_file, append_to_file,
apply_patch) prints a colored unified diff in the output, so you can see
exactly what changed:

```bash
[edit: src/foo.py] +3 -1
--- src/foo.py
+++ src/foo.py
@@ -12,3 +12,5 @@
 old line
+new line
```

Diff display is on by default. Disable it with `--no-diff`, or configure it
via the env file (`~/.config/lama_ole/lama_ole.env` or `./lama_ole.env`):

```ini
LAMA_OLE_SHOW_DIFF=false
```

### Inspecting Tools

Use `--help-tools` to see all loaded tools, their signatures, and which
environment variables they read:

```bash
python3 lama_ole.py --help-tools --tool tools.media_understanding_tools
```

Output:

```
Tool Module: tools.media_understanding_tools
  Environment Variables:
    LAMA_OLE_VISION_HOST: Ollama host for vision/audio models (defaults to --host value)
  Functions:
    image_describe(path: string, [model: string]) — Describe the contents of an image using a vision model
    image_ask(path: string, question: string, [model: string]) — Ask a specific question about an image
    image_ocr(path: string, [lang: string]) — Extract text from an image using OCR (requires tesseract)
    video_describe(path: string, [interval: number], [model: string]) — Describe a video by extracting frames
    video_scene_changes(path: string, [threshold: number]) — Detect scene changes with ffmpeg
    video_transcribe(path: string, [model: string]) — Extract audio and transcribe with Whisper
    video_ask(path: string, question: string, [interval: number], [model: string]) — Ask about a video
    audio_transcribe(path: string, [model: string]) — Transcribe speech with Whisper
    audio_ask(path: string, question: string, [model: string]) — Transcribe and answer a question
    list_vision_models() — List available vision models configured via --vision_model
```

### Multiple Tool Modules

```bash
python3 lama_ole.py --chat -m llama3.2:3b \
    --tool tools.example_tools \
    --tool tools.media_understanding_tools \
    --tool tools.web_tools
```

## Media Understanding Tools

The bundled `tools.media_understanding_tools` module provides image, video, and
audio comprehension via Ollama vision models, Whisper transcription, and OCR.

### Vision Models

Specify which vision models the tools should use with `--vision_model`
(repeatable). The first model is the default when the LLM doesn't pick one.

```bash
python3 lama_ole.py --chat -m llama3.2:3b \
    --tool tools.media_understanding_tools \
    --vision_model gemma3:12b --vision_model llava:13b
```

The LLM can call `list_vision_models()` to see which models are available, then
choose one by passing `model="gemma3:12b"` to any vision tool.

### Image Tools

| Tool | Description |
|------|-------------|
| `image_describe(path, [model])` | Describe image contents in detail |
| `image_ask(path, question, [model])` | Ask a specific question about an image |
| `image_ocr(path, [lang])` | Extract text via tesseract (default `eng`) |

### Video Tools

| Tool | Description |
|------|-------------|
| `video_describe(path, [interval], [model])` | Extract frames every `interval` seconds and describe each |
| `video_scene_changes(path, [threshold])` | Detect scene cuts with ffmpeg |
| `video_transcribe(path, [model])` | Extract audio and transcribe with Whisper |
| `video_ask(path, question, [interval], [model])` | Transcribe audio + analyze a mid-video frame |

### Audio Tools

| Tool | Description |
|------|-------------|
| `audio_transcribe(path, [model])` | Transcribe speech via Whisper |
| `audio_ask(path, question, [model])` | Transcribe and answer a question |

### Examples

```bash
# Describe an image
python3 lama_ole.py -m llama3.2:3b -i "Describe this image" \
    --tool tools.media_understanding_tools \
    --vision_model llava-phi3:3.8b

# Ask about a video
python3 lama_ole.py -m llama3.2:3b -i "What objects are in this video?" \
    --tool tools.media_understanding_tools

# Transcribe audio
python3 lama_ole.py -m llama3.2:3b -i "Transcribe this recording" \
    --tool tools.media_understanding_tools
```

## Bundled Tool Modules

| Module | Description | Tools |
|--------|-------------|-------|
| `tools.example_tools` | Example/reference tools | `get_weather`, `calculate`, `read_file` |
| `tools.media_understanding_tools` | Image, video, audio comprehension | `image_describe`, `image_ask`, `image_ocr`, `video_describe`, `video_scene_changes`, `video_transcribe`, `video_ask`, `audio_transcribe`, `audio_ask`, `list_vision_models` |
| `tools.dev_tools` | Development (filesystem, code, git) | `run_command`, `read_file`, `write_file`, `glob`, `grep`, `git_status`, etc. |
| `tools.dev_tools_safer` | Safer subset of dev tools | (limited operations) |
| `tools.web_tools` | Internet access | `web_fetch`, `web_search` |
| `tools.image_tools` | Basic image operations | image format conversion, resizing |
| `tools.video_tools` | Basic video operations | video format conversion, trimming |
| `tools.audio_tools` | Basic audio operations | audio format conversion |
| `tools.read_base64` | Base64 decoding | decode base64 strings |
| `tools.lsp_tools` | Language Server integration (code intelligence) | `lsp_start`, `lsp_open`, `lsp_hover`, `lsp_definition`, `lsp_references`, `lsp_completion`, `lsp_signature_help`, `lsp_document_symbols`, `lsp_workspace_symbols`, `lsp_diagnostics`, `lsp_status`, `lsp_stop` |

## LSP Tools

The `tools.lsp_tools` toolset gives the model IDE-style code intelligence by
talking to a real [Language Server](https://microsoft.github.io/language-server-protocol/)
over stdio (JSON-RPC 2.0 with `Content-Length` framing). All tools are
read-only; files are still edited with the `edit` toolset and re-synced into
the server automatically before every query.

```bash
python3 lama_ole.py -m <model> --chat --tool tools.lsp_tools --tool tools.edit
```

- `lsp_start` — start a session for a language (`python`, `typescript`, `rust`,
  `go`, `cpp`, `c`, `json`, ...). Sessions are long-lived; queries auto-start a
  session for the file's language, so `lsp_start` is optional.
- `lsp_open` / automatic sync — the server always sees the on-disk file content
  (mtime/size checks); edits made by other tools are picked up before each query.
- `lsp_hover`, `lsp_definition`, `lsp_references`, `lsp_completion`,
  `lsp_signature_help`, `lsp_document_symbols`, `lsp_workspace_symbols` — the
  standard code-intelligence queries. Positions are 0-based; character offsets
  are UTF-16 code units (per the LSP spec).
- `lsp_diagnostics` — errors/warnings cached from the server's
  `publishDiagnostics` push notifications (no round-trip).
- `lsp_status` / `lsp_stop` — inspect and shut down sessions.

Server commands come only from a built-in table or the `LAMA_OLE_LSP_SERVERS`
environment variable — the model can never execute an arbitrary command:

```bash
export LAMA_OLE_LSP_SERVERS='{"python": "pyright-langserver --stdio", "rust": ["rust-analyzer"]}'
```

If a server crashes, the next query auto-restarts it once; a second crash in a
row asks you to run `lsp_start` again. See `llm_blueprint/lsp_tools/` for the
design docs.

## Model Transfer

lama_ole can transfer models between ollama instances using `--transfer`.

### Local source to remote destination

```bash
python3 lama_ole.py -m gemma4:12b --transfer localhost other_host
```

The source must be `localhost` (the machine running lama_ole). The tool reads
model blobs directly from the local ollama model store and uploads them to the
destination ollama instance via its API.

### Remote source via blob server

On the source machine, start the blob HTTP server:

```bash
python3 lama_ole.py --serve-blobs --blob-port 9999
```

On the orchestrator machine, point `--transfer` at the blob server URL:

```bash
python3 lama_ole.py -m gemma4:12b \
    --transfer http://192.168.1.100:9999 other_host
```

The orchestrator downloads manifests and blobs from the source's blob server,
then uploads them to the destination's ollama API. Blobs are streamed in
chunks to avoid memory spikes.

### One-liner with SSH tunnel

```bash
ssh user@remote_source "lama_ole --serve-blobs --blob-port 9999" &
python3 lama_ole.py -m gemma4:12b \
    --transfer http://remote_source:9999 other_host
```

### How it works

1. lama_ole reads the model manifest and all blob digests from the source
2. Each blob is uploaded to the destination via `POST /api/blobs/`
3. The FROM path in the Modelfile is rewritten to match the destination's
   model store
4. The model is created on the destination via `POST /api/create`

## Chat Commands

In chat mode (`--chat`), lines starting with `/` are commands:

| Command | Description |
|---------|-------------|
| `/feed <path>` | Read a file and send its content as a message |
| `/new` | Start a new session (the previous session is preserved and can be restored with `/resume`) |
| `/compact [auto on\|off]` | Compact the context now (summarize older turns, keep recent verbatim), or toggle/show auto-compaction |
| `/model <name>` | Switch to a different model |
| `/plan` | Switch to plan mode (write tools blocked until `/build`) |
| `/build` | Switch to build mode (full tools, changes allowed) |
| `/save <path>` | Save the conversation to a JSON file (model, messages, active skill, system prompt and loaded toolsets) |
| `/load <path>` | Load a conversation from a JSON file (restores the active skill, system prompt and re-loads toolsets) |
| `/resume [match]` | Resume a saved session; without an argument it lists sessions and prompts, with a session-id or title substring it loads directly |
| `/sessions [all]` | List sessions in the current directory (1 = oldest, newest shown first); `all` includes every project. `rm` subcommand deletes session files |
| `/sessions rm <N \| -N \| a..b \| all>` | Delete session file(s) by number (`N` = exactly session N, `-N` = the N most recent, `a..b` = an inclusive range, `all` = everything), space-separated selectors allowed; always asks for confirmation |
| `/stats` | Show the current model, the last turn's per-round breakdown (time, tokens, tok/s), and session averages per model |
| `/rename <new title>` | Rename the current session (persists across autosaves) |
| `/rename <id-prefix> <new title>` | Rename a stored session by session-id prefix |
| `/tools loaded` | List loaded toolsets and their tools |
| `/tools available` | List toolsets available to load |
| `/tools show <toolset>` | List all tools of one toolset |
| `/tools all` | List all tools of all toolsets |
| `/tools load <toolset> [<toolset> ...]` | Load one or more toolsets at runtime |
| `/tools unload <toolset> [<toolset> ...]` | Unload one or more toolsets at runtime |
| `/skill list` | List available skills |
| `/skill load <name-or-path> [<name-or-path> ...]` | Load one or more skills into the system role |
| `/skill unload` | Unload the active skill |
| `/skill show` | Show the active skill |
| `/systemprompt [show]` | Show the current system prompt |
| `/systemprompt <file>` | Load a system prompt from a file |
| `/systemprompt unset` | Unset the system prompt (back to default) |
| `/context` | Show context usage (tokens/window/percentage + breakdown), or `/context on` / `/context off` to toggle the meter |
| `/history [<selector> ...]` | Show conversation history entries with numbers (see below) |
| `/cut <N> \| <a..b> \| undo` | Trim the conversation history to a selection (see below) |
| `/help` | Show this help message |
| `/exit`, `/quit` | Exit the chat |

Bare `/tools` prints the tool subcommand usage. Bare `/skill` prints the skill
subcommand usage. Bare `/systemprompt` prints the current system prompt.

### Context-window meter

In chat mode a context-window usage meter is shown by default:

* The prompt shows a live gauge, e.g. `[ctx 12,345/32,768 ████░░░░░░ 37%] `,
  which is green below 70% usage, yellow from 70%, and red from 90%. It
  updates after every turn.
* `/context` prints the exact usage plus a per-category breakdown
  (system/user/assistant/tool), estimated and scaled to match the real token
  count; `/context off` and `/context on` toggle the meter.
* Before each turn a warning is printed when the typed message is predicted
  to overflow the window.
* The last-known usage is saved with the session and restored on `/resume` /
  `/load`, so the gauge is meaningful right away. The count is exact when the
  session's model is unchanged; after a model change (or a mid-session
  `/model`) it is shown as an estimate with a tilde, e.g.
  `[ctx ~12,345/32,768 ████░░░░░░ ~37%] `. The window size (`num_ctx`) does
  not affect the count — only the percentage, which recomputes against the
  current window.

The window size is resolved in order: `--num_ctx`, `LAMA_OLE_CTX_SIZE`, the
running model's allocated context (`ollama ps`), the model's `num_ctx`
parameter, the model's declared context length, otherwise unknown (token
counts are shown without a percentage).

### Context compaction

When the conversation grows too large for the context window, `/compact`
summarizes the older turns into a single structured summary while keeping the
most recent turns verbatim (mirroring how opencode compacts its sessions):

* Older turns are serialized into labeled text (`[User]:`, `[Assistant]:`,
  `[Assistant tool call]:`, `[Tool result]:`, ...), tool results are truncated
  to 2000 characters, and the head is handed to the summarizer model.
* The summarizer streams an **anchored** Markdown summary (Objective, Important
  Details, Work State, Next Move, Relevant Files). If the conversation was
  already compacted, the previous summary is passed as `<previous-summary>` and
  updated instead of being nested.
* The summarized head is replaced by a `compacted` user message and the recent
  tail (last 2 turns, bounded by a token budget) stays verbatim. The meter
  resets so usage is recomputed from the next request.
* Summaries use the model from `--auto-compact-model`, or the chat model by
  default. Confirmation is always requested before tokens are spent.

Auto-compaction triggers after a turn when the context usage crosses the
threshold and asks for confirmation:

| Option / env var | Default | Description |
|------------------|---------|-------------|
| `--auto-compact` / `LAMA_OLE_AUTO_COMPACT` | off | Enable auto-compaction on threshold crossing |
| `--auto-compact-threshold` / `LAMA_OLE_AUTO_COMPACT_THRESHOLD` | `0.75` | Fraction of the window (in `(0, 1]`) that triggers compaction |
| `--auto-compact-model` / `LAMA_OLE_AUTO_COMPACT_MODEL` | chat model | Model used to produce summaries |

In chat mode `/compact auto on` / `/compact auto off` toggle auto-compaction
at runtime and `/compact auto` shows the current setting.

Compaction configuration is saved with the session and restored on `/resume`.

Tab completion is enabled in interactive mode: commands, `/tools`, `/skill`,
`/compact` and `/systemprompt` subcommands, and file paths (for `/feed`,
`/save`, `/load`, `/skill load` and `/systemprompt`) are completed with Tab.
Completion needs the `readline` module and is skipped automatically when stdin
is not a terminal.

### Plan / build modes

The chat agent runs in one of two opencode-style modes:

* **Build** (default) — full access to every loaded tool; changes are allowed.
* **Plan** — the model is told to analyze and plan without making changes. All
  loaded tools remain advertised so the model knows what exists, but write tools
  refuse to execute: a call returns a plan-mode notice instead of running.
  Read-only tools (modules marked `__tool_readonly__ = True`, e.g.
  `tools.dev_tools_readonly`, `tools.web_tools`, `tools.media_understanding_tools`)
  still work normally.

Switch modes with **Shift+Tab** (a single keystroke that toggles between `/plan`
and `/build`), or type `/plan` / `/build`. The prompt always shows the current
mode: a green `[build] ` or a yellow `[plan] `. The mode is remembered in saved
sessions, so resuming a plan session stays in plan mode. Start in a given mode
with `--mode plan` (`LAMA_OLE_MODE=plan`). To make your own tool module
plan-safe, add a module-level `__tool_readonly__ = True`.

**Shift+Tab also works mid-turn** — while the model is streaming or a tool is
running, it switches mode without interrupting the current response. The switch
takes effect immediately for tool execution: any write tool that arrives after
the switch is blocked (the plan-mode notice is fed back to the model) while the
advertised tool list stays unchanged. Printable keys typed mid-turn are captured
and replayed into the next prompt's line buffer; Enter and arrow keys are
ignored while the model is working.

## Sessions

Chat sessions are saved automatically. Each chat run is recorded after every
turn and on exit, so you can leave and resume later without manual `/save`
and `/load`.

* **Storage**: `~/.local/share/lama_ole/sessions/` (respects `XDG_DATA_HOME`,
  or override with `LAMA_OLE_SESSION_DIR`). One directory per project (the
  working directory encoded into a slug plus a short hash of the real path:
  `/home/me/proj` → `home-me-proj-<hash>`; the hash guarantees similar names
  like `lama_ole` vs `lama-ole` never collide), each session its own
  `<session-id>.json` file with 0600 permissions. The real directory path is
  stored inside the file.
* **Auto-resume**: starting `--chat` restores the most recent session for the
  current directory and prints a notice. The restored conversation is then
  replayed as a numbered listing that looks exactly like `/history` — the same
  `[N] [ts]` prefixes, role labels and colors (the views use the per-view
  format templates described in "History and cutting"). If the session model
  differs from the CLI `-m`, you are asked which to keep (session / CLI /
  abort).
  `/resume` and `/load` replay the history the same way.
* **Opt out**: the two behaviors are independent toggles, both on by default:
  * `--no-resume` (or `LAMA_OLE_RESUME=false`) disables auto-loading.
  * `--no-autosave` (or `LAMA_OLE_AUTOSAVE=false`) disables writing session
    files.
  `/resume` and `/sessions` still work for manual recovery either way.
* **Renames/moves**: if a project directory is renamed, its sessions no
  longer match the new path automatically. Run `/resume` — sessions recorded
  elsewhere are listed under "Other projects" and resuming one re-associates
  it to the current directory.
* **Listing**: `/sessions` lists the current directory's sessions only;
  `/sessions all` shows every project, grouped by its recorded cwd. Sessions
  are numbered like `/history` (1 = oldest, highest = newest) but displayed
  newest first, so the newest session always sits on top. `*` marks the active
  session, and the footer prints the sessions directory plus the active
  session's file path. Bare `/sessions rm` prints the full listing and prompts;
  with a selection it deletes the matching file(s) after a `y/N` confirmation
  (the numbers are a positional alias — the session-id is the stable identity
  for `/resume` and `/rename`).
* **`/new`**: archives the current session (leaving it restorable) and
  starts a fresh one.
* **`/stats`**: shows the current model, the last turn's per-round breakdown
  (time, in/out tokens, tok/s), and session averages broken down per model.
  Averages and the last-turn breakdown are saved with the session (autosave,
  `/save`, `/load`, `/resume`) and restored on resume.
* **Titles**: sessions are titled from the first user message by default.
  `/rename <new title>` overrides it for the current session (persisted across
  autosaves), and `/rename <id-prefix> <new title>` renames any stored session
  by its session-id prefix. A renamed title is kept as-is; unrenamed sessions
  keep deriving from their first message.
* **`/save <path>` / `/load <path>`**: explicit portable snapshots for
  sharing or backup; they remain independent of the automatic sessions.
  `/load` first archives the current conversation to its auto-save slot, so a
  resumed session is never silently overwritten.

### History and cutting

Every conversation message is numbered from **1** (oldest) up to **M**
(newest), where M is the total number of messages — the same numbering used by
`/history` and `/cut`. System messages are hidden from `/history` and are never
removed by `/cut`.

A resumed session is replayed as a numbered listing that looks exactly like
`/history` (same prefixes, labels and colors), so the two views never diverge.
How lines are rendered is driven by **line templates** set through environment
variables:

- `LAMA_OLE_FORMAT_HISTORY` — the `/history` view.
- `LAMA_OLE_FORMAT_REPLAY` — session replay.
- `LAMA_OLE_FORMAT_OUTPUT` — the live `--chat` assistant stream. It defaults
  to raw streamed text, so leaving it unset preserves the current chat output.

Each entry type has one template. A value is either a bare template applied to
every visible type, or semicolon-separated `type=template` pairs. Types may be
named by their canonical key or a role-name alias: `user`, `output`
(alias `assistant`), `thinking`, `toolcall`, `tool_result` (alias `tool`),
`compacted`. An empty template (`type=`) hides that entry type. The display
names behind the `{role}` token are customized with `name.<role>=<label>`
pairs (roles: `user`, `assistant`, `thinking`, `toolcall`, `tool`,
`compacted`); a toolcall entry composes its name from `name.toolcall` +
`name.tool` when it carries a summary. Tokens:

- `{num}` — the entry number (`1` = oldest, `M` = newest)
- `{ts}` — `[<time>] ` when the message carries a timestamp, else empty
- `{role}` — the display name (`USER`, `ASSISTANT (TOOLCALL)`, `TOOL`, ...);
  override it with `name.<role>=<label>` rather than inlining labels into
  every template
- `{text}` — the message text (tool calls render as `[data from <name>: <args>]`)
- `{tool}` / `{args}` — the tool name / arguments (tool entries only)

The default is `[{num}] {ts}{role}: {text}` for all visible types and an empty
template for `tool_result` (hidden). An invalid template falls back to the
default and prints a warning once. Colors are applied per entry type whenever
color mode is on; there is no per-line color setting. Examples:

```sh
LAMA_OLE_FORMAT_HISTORY="[{num}] {ts}{role}: {text}"     # built-in /history default
LAMA_OLE_FORMAT_REPLAY="user={num} You: {text};assistant={num} Bot: {text}"
LAMA_OLE_FORMAT_OUTPUT="{text}"                          # raw live chat output (default)
LAMA_OLE_FORMAT_OUTPUT="[{num}] {role}: {text}"          # format live chat output
```

`/history -t` forces tool results on for one listing: a hidden `tool_result`
template is replaced by the default template when no explicit `tool_result`
value is set.
The live chat stream has its own `LAMA_OLE_FORMAT_OUTPUT` template; it is
independent from the `/history` and replay views.

#### `/history`

`/history` lists the conversation with its message numbers, in order from
oldest to newest. By default it shows user messages, assistant output, thinking
and tool calls; tool **responses** are shown only with `-t`.

Every entry is prefixed with the time the event happened, e.g.
`[7] [2026-08-09 09:01:00] USER: ...`. Tool calls show the concrete function
name and arguments (`TOOL: [data from read_file: path='lama_ole/AGENTS.md']`),
not just an empty `ASSISTANT (TOOLCALL)` marker; the full tool response data
still requires `-t` (or a `tool_result` line template in
`LAMA_OLE_FORMAT_HISTORY`).

| Command | Shows |
|---------|-------|
| `/history` | all entry types with a non-empty template |
| `/history -t` | all entry types including tool responses |
| `/history -10` | the last 10 entries |
| `/history 10` | the first 10 entries |
| `/history 10 -10` | the first 10 and the last 10 entries |
| `/history a..b` | the entries numbered `a` to `b` |
| `/history 5 c..d -6` | the first 5 entries, a range, and the last 6 entries |

Ranges and numbers can be combined freely in one command.

#### `/cut`

`/cut` trims the conversation down to the entries you name — everything else
is removed. The removed messages are stored so they can be restored with
`/cut undo`. System messages are never removed.

| Command | Keeps | Removes |
|---------|-------|---------|
| `/cut N` | entries `N..M` (from entry N to the newest) | the older `1..N-1` |
| `/cut -N` | the last N entries | everything older |
| `/cut a..b` | only the entries numbered `a` to `b` | everything else |
| `/cut undo` | — | restores what the last `/cut` removed |

Example: after a `/cut 3`, the conversation is trimmed to entries `3..M`; the
first two entries are gone. `/cut -3` keeps only the three most recent entries.
`/cut undo` brings the removed messages back, and a later `/cut` replaces the
undo buffer (only the most recent cut is undoable). Note the asymmetry with
`/history`: `/history 3` *shows* the first 3 entries, while `/cut 3` *keeps*
from entry 3 onward.

#### Interruptions (Ctrl-C)

When a turn is interrupted with Ctrl-C, only the incomplete part of that turn
is removed from the history. Your last message — and the system prompt — are
kept, and so are every **completed** tool round: a tool call that already
returned its result stays visible in `/history`, so its context is preserved
for the next turn. Only a tool call that was interrupted while still running
(and its partial results) is dropped, along with whatever the model had started
to generate when you pressed Ctrl-C.

## Configuration Options

| Flag | Description | Default |
| :--- | :--- | :--- |
| `-h, --help` | Show help message and exit | |
| `-V, --version` | Show program version and exit | |
| `--host HOST` | Ollama instance host | `http://localhost:11434` |
| `-m, --model MODEL` | Model name to use | (required) |
| `-i, --input TEXT` | Input string for the model | |
| `-f, --inputfile PATH` | Read input from a file | |
| `--stdin` | Read input from standard input | |
| `-o, --outlog PATH` | Log main output to file | |
| `--toolcalllog PATH` | Log tool calls and results to a separate file | |
| `--chatinputlog PATH` | Log chat REPL input to a separate file | |
| `--logndjson PATH` | Log each conversation message as an NDJSON line | |
| `-t, --thinking` | Show model's thought process | |
| `--thoughtlog PATH` | Log thoughts to file (independent of `-t`) | |
| `--temperature FLOAT` | Sampling temperature | `0.0` |
| `--num_ctx INT` | Context window size | (Ollama default) |
| `--num_gpu INT` | GPU layers to use | (Ollama default) |
| `--keep_alive DURATION` | Keep model in memory (`5m`, `1h`) | (Ollama default) |
| `--chat` | Start interactive chat REPL | |
| `--resume` / `--no-resume` | Auto-resume the most recent session for the current directory on startup | `--resume` |
| `--autosave` / `--no-autosave` | Auto-save the chat session after every turn and on exit | `--autosave` |
| `--tool MODULE` | Load tool module (repeatable) | |
| `--skill PATH` | Load skill text into system role (repeatable; files concatenated) | |
| `--vision_model MODEL` | Vision model for media tools (repeatable) | (auto-detect) |
| `--help-tools` | Show loaded tool documentation and exit | |
| `--safe` | Confirm before dangerous tool operations | |
| `--mode MODE` | Chat agent mode: `build` or `plan` (write tools blocked in plan) | `build` |
| `--max_tool_rounds N` | Max tool-calling rounds | (no limit) |
| `--max_tool_rounds_continuation` | Behavior at limit: `ask` or `fallback` | `ask` |
| `-l, --list` | List all available models | |
| `--ps` | List all running models | |
| `--stop MODEL` | Stop/unload a running model from memory | |
| `--ollama_websearch` | Activate Ollama's built-in web search tool (requires Ollama 0.5+) | |
| `--transfer SOURCE DEST` | Transfer a model from SOURCE to DEST ollama instance | |
| `--serve-blobs` | Start a blob HTTP server for remote transfer source | |
| `--blob-host HOST` | Host to bind blob server | `127.0.0.1` |
| `--blob-port PORT` | Port for blob server | random |
| `--system_prompt TEXT` | System prompt passed to the model | |
| `--system_prompt_file PATH` | Read system prompt from a file | |
| `--no_safety_system_prompt` | Disable safety system prompt; enables potential takeover when tools are used (placed after any user-provided system prompt) | |
| `--debug` | Initialize the environment and enter an interactive Python REPL for debugging | |
| `--color MODE` | Colorize user input, thinking, and LLM output: `auto` (TTY only), `always`, `never`/`none` | `auto` |
| `--ctx-meter` / `--no-ctx-meter` | Show the context-window usage meter in chat mode | on |
| `--auto-compact` / `--no-auto-compact` | Enable auto-compaction on threshold crossing | off |
| `--auto-compact-threshold FLOAT` | Fraction of the window (in `(0, 1]`) that triggers auto-compaction | `0.75` |
| `--auto-compact-model MODEL` | Model used to produce compaction summaries | chat model |
| `-v` to `-vvv` | Verbosity level (repeat for more) | silent |

### Verbosity Levels

| Level | Output |
|-------|--------|
| (default) | Silent — no debug output |
| `-v` | Tool call names + truncated results (500 chars) |
| `-vv` | Full tool results + messages payload before API calls |
| `-vvv` | Raw streaming chunks as they arrive |

## Writing Custom Tools

Tools are Python functions decorated with `@tool` from `tool_base`:

```python
from tool_base import tool


@tool(description="Multiply two numbers")
def multiply(a: int, b: int) -> int:
    return a * b


@tool(description="Get the population of a city")
def get_population(city: str) -> str:
    return f"Population of {city}: 2.5 million"
```

Parameter types are inferred from annotations. For complex schemas, pass
explicit `params`:

```python
@tool(
    description="Search the web",
    params={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    },
)
def web_search(query: str) -> str:
    ...
```

Load your module:

```bash
python3 lama_ole.py -m llama3.2:3b -i "search for python tutorials" --tool mytools
```

### Documenting Environment Variables

Tools that read environment variables should define a module-level
`__tool_env__` dict. These are displayed by `--help-tools`:

```python
__tool_env__ = {
    "MY_API_KEY": "API key for external service",
}
```

## Configuration

Most CLI flags can be given defaults via environment variables, so you don't
have to repeat a long parameter list on every invocation.

### Precedence

```
CLI flag  >  shell env var  >  ./lama_ole.env (project)  >  ~/.config/lama_ole/lama_ole.env (user)  >  built-in default
```

- Values are stored in `KEY=VALUE` files, one per line. Blank lines and lines
  starting with `#` are ignored; optional surrounding quotes are stripped.
- The **project file** (`./lama_ole.env` in the current working directory)
  overrides the **user file** (`~/.config/lama_ole/lama_ole.env`).
- Anything already set in your shell environment wins over both files.
- An **empty value** (`LAMA_OLE_MODEL=`) means "unset" and falls back to the
  built-in default.
- Invalid values (e.g. `LAMA_OLE_NUM_CTX=banana`) print a warning to stderr
  and fall back to the default.

### Example

```bash
# ~/.config/lama_ole/lama_ole.env
LAMA_OLE_CHAT=true
LAMA_OLE_MODEL=llama3.2:3b
LAMA_OLE_THINKING=true
LAMA_OLE_SAFE=true
LAMA_OLE_TOOL=tools.example_tools tools.web_tools
LAMA_OLE_TEMPERATURE=0.2
```

```bash
python3 lama_ole.py                                # chat, llama3.2:3b, thinking+safe, example/web tools
python3 lama_ole.py --no-thinking                 # same, but thinking off
python3 lama_ole.py --no-chat -i "explain X"      # one-shot query, thinking still on
python3 lama_ole.py --tool tools.audio_tools      # config tools + audio_tools (merged)
python3 lama_ole.py --ignore-config-tools --tool tools.audio_tools
                                                  # audio_tools only
```

Because `--chat`, `--thinking`, `--safe` and `--ollama_websearch` accept both
`--flag` and `--no-flag`, they can be turned on *or off* per run regardless of
the configured default.

### Environment Variables

| Variable | Type | Flag |
| :--- | :--- | :--- |
| `LAMA_OLE_HOST_OLLAMA` | string | `--host` |
| `LAMA_OLE_HOST_LLAMACPP` | string | `--llamacpp-host` |
| `LAMA_OLE_HOST_LLAMACPP_API_KEY` | string | — (llama.cpp Bearer auth) |
| `LAMA_OLE_LLAMACPP_AUTOSTART` | boolean | `--llamacpp-autostart` / `--no-llamacpp-autostart` |
| `LAMA_OLE_LLAMACPP_BIN` | string | — (llama-server binary path) |
| `LAMA_OLE_LLAMACPP_MODELS_DIR` | string | — (models dir for the autostarted router) |
| `LAMA_OLE_LLAMACPP_ARGS` | string | — (extra llama-server args) |
| `LAMA_OLE_LLAMACPP_STOP_ON_EXIT` | boolean | — (kill autostarted server on exit) |
| `LAMA_OLE_MODEL` | string | `-m, --model` |
| `LAMA_OLE_TEMPERATURE` | number | `--temperature` |
| `LAMA_OLE_NUM_CTX` | integer | `--num_ctx` |
| `LAMA_OLE_NUM_GPU` | integer | `--num_gpu` |
| `LAMA_OLE_KEEP_ALIVE` | string | `--keep_alive` |
| `LAMA_OLE_CHAT` | boolean | `--chat` / `--no-chat` |
| `LAMA_OLE_THINKING` | boolean | `-t, --thinking` / `--no-thinking` |
| `LAMA_OLE_SAFE` | boolean | `--safe` / `--no-safe` |
| `LAMA_OLE_MODE` | string | `--mode` (`build` or `plan`) |
| `LAMA_OLE_OLLAMA_WEBSRCH` | boolean | `--ollama_websearch` / `--no-ollama_websearch` |
| `LAMA_OLE_VERBOSE` | integer | `-v, --verbose` (CLI `-v` adds to it) |
| `LAMA_OLE_COLOR` | string | `--color` (`auto`, `always`, `never` or `none`) |
| `LAMA_OLE_CTX_METER` | boolean | `--ctx-meter` / `--no-ctx-meter` |
| `LAMA_OLE_CTX_SIZE` | integer | (config-only, no flag) — force the meter's context window |
| `LAMA_OLE_AUTO_COMPACT` | boolean | `--auto-compact` / `--no-auto-compact` |
| `LAMA_OLE_AUTO_COMPACT_THRESHOLD` | number | `--auto-compact-threshold` (must be in `(0, 1]`) |
| `LAMA_OLE_AUTO_COMPACT_MODEL` | string | `--auto-compact-model` |
| `LAMA_OLE_TOOL` | space/comma-separated list | `--tool` (CLI appends, deduped) |
| `LAMA_OLE_VISION_MODEL` | space/comma-separated list | `--vision_model` (CLI replaces) |
| `LAMA_OLE_MAX_TOOL_ROUNDS` | integer | `--max_tool_rounds` |
| `LAMA_OLE_MAX_TOOL_ROUNDS_CONTINUATION` | string | `--max_tool_rounds_continuation` |
| `LAMA_OLE_SYSTEM_PROMPT` | string | `--system_prompt` |
| `LAMA_OLE_SYSTEM_PROMPT_FILE` | string | `--system_prompt_file` |
 | `LAMA_OLE_COLOR_PROMPT` | color spec | (config-only, no flag) |
 | `LAMA_OLE_COLOR_THINKING` | color spec | (config-only, no flag) |
 | `LAMA_OLE_COLOR_OUTPUT` | color spec | (config-only, no flag) |
 | `LAMA_OLE_COLOR_INPUT` | color spec | (config-only, no flag) |
 | `LAMA_OLE_COLOR_METER_LOW` | color spec | (config-only, no flag) |
 | `LAMA_OLE_COLOR_METER_MID` | color spec | (config-only, no flag) |
 | `LAMA_OLE_COLOR_METER_HIGH` | color spec | (config-only, no flag) |
| `LAMA_OLE_FORMAT_HISTORY` | line template | (config-only, no flag) — per-view override for `/history` |
| `LAMA_OLE_FORMAT_REPLAY` | line template | (config-only, no flag) — per-view override for session replay |
| `LAMA_OLE_FORMAT_OUTPUT` | line template | (config-only, no flag) — per-view override for live `--chat` assistant output |

The `LAMA_OLE_COLOR_*` variables customize the ANSI colors used for the chat
prompt, your typed input, the thinking stream, the LLM output, and the context
meter (green below 70% usage, yellow from 70%, red from 90%). Each accepts a
comma-separated color spec: a named foreground color (`black`…`white`, `bright_*`,
`grey`/`gray`), a 256-color number (`0`–`255`), a hex value (`#rrggbb`), plus
attributes (`bold`, `italic`, `underline`, `dim`, `reverse`). Examples:
`bold,green`, `#ff8700`, `bright_cyan`. Use `default` or `none` to restore the
built-in color. Your typed input is echoed in the input color by appending its
escape code to the prompt (so it matches the replay); the default is `bright_cyan`
while the model output defaults to `bright_white`.
These are theme preferences, so they are configured via the env/config files
only (the CLI keeps just the `--color` on/off switch); an invalid value prints a
warning and keeps the built-in default.

Booleans accept `1/true/yes/on` and `0/false/no/off` (case-insensitive).
`--tool` values are merged with the configured default (config first, deduplicated)
unless `--ignore-config-tools` is given, which uses only the CLI `--tool` values.
`--vision_model` always replaces the configured default when given on the command
line.

Tool modules may read additional environment variables, e.g.
`LAMA_OLE_VISION_HOST` (see `--help-tools`). These can live in the same
config files.

## Troubleshooting

- **Connection Error** — Ensure Ollama is running and `--host` matches your
  setup (default `http://localhost:11434`).
- **File Exists Error** — The script refuses to overwrite existing files.
  Remove the target file first or use a different path.
- **Missing Library** — Run `pip install ollama`. For media tools, also
  `pip install Pillow`.
- **Tool not found** — Use dotted module names, not file paths:
  `--tool tools.example_tools` (not `--tool tools/example_tools.py`).
- **Media: no vision model found** — Use `--vision_model MODEL` to specify
  which installed Ollama models are vision-capable. Run `--help-tools` with
  your tool module to verify configuration.
- **Chat errors** — Model errors in chat mode are caught gracefully and
  printed without exiting the REPL.

## License

This project is open-source and available under the

[Apache License Version 2.0, January 2004](http://www.apache.org/licenses/).
