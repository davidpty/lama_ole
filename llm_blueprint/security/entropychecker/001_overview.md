# Entropy Checker — Overview

## Purpose

The entropy checker prevents binary / random data from leaking into the LLM context.
When a tool accidentally opens a binary file (image, archive, compiled object, …) or
reads raw stdin that is not valid UTF-8 text, the garbage bytes would be fed to the
model and cause confusion, wasted tokens, or even prompt-injection via control codes.

The entropy checker sits between every data source (file read, stdin feed, tool output)
and the LLM message buffer. It inspects incoming bytes blockwise, estimates their
entropy, and either **accepts** them as valid text or **refuses** them as too random.

## Design Principles

| Principle | Rationale |
|-----------|-----------|
| Blockwise streaming | We never need to hold the whole input in memory before deciding — early rejection is faster. |
| Per-source reset | Each file / stdin stream gets its own checker instance so a binary file does not poison a subsequent text read. |
| Compression fallback | Pure statistical entropy estimation can be fooled by compressed-but-valid data (e.g. gzip-encoded source). A zip-ratio check catches true randomness that passes the pattern test. |
| Stateless core + stateful wrapper | The pure analysis logic lives in functions (easy to unit-test); a class wraps it with buffers, thresholds and reset semantics. |

## Architecture at a Glance

```
lama_ole/
├── security/
│   ├── __init__.py
│   └── entropychecker.py          ← the module we will write
├── documentation/security/entropychecker/
│   ├── 001_overview.md            ← this file
│   ├── 002_class_outline.md       ← class design & API
│   ├── 003_implementation_plan.md ← ordered task list
│   ├── 004_integration_points.md  ← where to wire it in
│   └── 005_testing_strategy.md    ← how to verify it works
```

## Core Idea (one paragraph)

The entropy checker maintains a sliding window over the incoming byte stream.
It classifies each byte into one of three categories:

1. **Safe printable** — `[[:alnum:]]`, whitespace, or common programming punctuation (`!@#$%^&*()-_=+[]{}|;:'",.<>?/\`~`).
2. **Control / unknown** — anything else (including high bytes ≥ 0x80 that are not valid UTF-8 continuations).

From these counts it derives a *relation variable* — the ratio of safe-to-total bytes in the current window.
If this ratio drops below a threshold, or if the byte-frequency distribution shows too many unique values,
the checker flags the stream as suspicious.

When the accumulated input exceeds a size limit (default 64 KiB), the checker additionally:

1. Zips the buffered content with `zlib.compress`.
2. Computes `ratio = len(compressed) / len(original)`.
3. If `ratio > 0.95` (i.e. compression barely helps → data is incompressible / random), **refuses** the entire buffer.

Valid text compresses well; true binary noise does not. This two-layer check (pattern + compression)
is robust against both obvious and subtle binary leaks.

## Quick Reference: Thresholds (tunable)

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `window_size` | 1024 | Sliding window for pattern analysis (bytes) |
| `safe_ratio_threshold` | 0.85 | Minimum fraction of safe bytes to pass pattern check |
| `unique_byte_threshold` | 150 | Max unique byte values in window before flagging (64 caused false positives on Unicode text) |
| `zip_size_limit` | 65536 | Accumulated bytes after which zip test activates |
| `zip_ratio_threshold` | 0.95 | Above this → data is too random, refuse |
