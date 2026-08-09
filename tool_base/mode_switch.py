"""Mid-turn mode-switching support.

The chat REPL normally only reads stdin at the prompt (via readline). To let a
user switch plan/build mode *while* the model is streaming or a tool is running,
we need to capture the Shift+Tab key sequence (``ESC [ Z``) without readline
active. That requires switching fd 0 into non-canonical ("cbreak") mode for the
duration of a turn: ``ISIG`` stays on so Ctrl-C keeps raising KeyboardInterrupt,
but ``ICANON``/``ECHO`` are off.

Two pure, unit-testable pieces are separated from the terminal plumbing:

* ``EscapeSequenceParser`` - recognizes a complete ``ESC [ Z`` from raw bytes.
* ``TypeAheadBuffer`` - keeps printable bytes typed mid-turn (applying
  backspaces, dropping control chars and escape sequences) so they can be
  replayed into the next prompt's readline line.

``ModeHotkeyListener`` wires those to the terminal. When stdin is not a tty (or
``termios`` is unavailable, e.g. Windows) every method degrades to a no-op, so
the rest of the codebase can call it unconditionally.
"""

import os
import select
import sys
import threading
import time

try:
    import termios
except ImportError:  # pragma: no cover - non-POSIX platforms
    termios = None


class EscapeSequenceParser:
    """Recognizes ``ESC [ Z`` (Shift+Tab) from a stream of raw bytes.

    ``feed(byte)`` returns True exactly once when a complete Shift+Tab
    sequence has been read; any other sequence is consumed and ignored.
    """

    _IDLE = 0
    _ESC = 1
    _ESC_BRACKET = 2

    def __init__(self):
        self._state = self._IDLE

    def feed(self, byte: int) -> bool:
        if self._state == self._IDLE:
            if byte == 0x1B:
                self._state = self._ESC
        elif self._state == self._ESC:
            if byte == 0x5B:  # '['
                self._state = self._ESC_BRACKET
            elif byte == 0x1B:
                pass  # stay in ESC, a fresh escape sequence
            else:
                self._state = self._IDLE
        else:  # _ESC_BRACKET
            self._state = self._IDLE
            if byte == 0x5A:  # 'Z'
                return True
        return False

    def reset(self) -> None:
        self._state = self._IDLE


class TypeAheadBuffer:
    """Accumulates printable text typed mid-turn for replay at the next prompt.

    Backspace deletes the previous character; control characters (including
    Enter and other ``ESC [ ...`` sequences) are dropped; the bytes typed for a
    handled Shift+Tab are naturally skipped because escape sequences are not
    kept. Incremental UTF-8 decoding keeps multi-byte characters intact even
    when their bytes arrive in separate reads.
    """

    _IDLE = 0
    _ESC = 1
    _CSI = 2

    def __init__(self):
        self._chars = []
        self._pending = b""
        self._state = self._IDLE

    def feed(self, data: bytes) -> None:
        self._pending += data
        try:
            decoded = self._pending.decode("utf-8")
        except UnicodeDecodeError:
            return
        self._pending = b""
        for ch in decoded:
            code = ord(ch)
            if self._state == self._IDLE:
                if ch == "\x1b":
                    self._state = self._ESC
                    continue
                if code in (0x7F, 0x08):  # backspace (DEL / BS)
                    if self._chars:
                        self._chars.pop()
                    continue
                if code < 0x20:
                    continue
                self._chars.append(ch)
            elif self._state == self._ESC:
                if code == 0x5B:  # '[' starts a CSI sequence
                    self._state = self._CSI
                else:
                    self._state = self._IDLE
            else:  # _CSI: consume params until the final byte (0x40-0x7e)
                if 0x40 <= code <= 0x7E:
                    self._state = self._IDLE

    def drain(self) -> str:
        text = "".join(self._chars)
        self._chars = []
        self._pending = b""
        self._state = self._IDLE
        return text


class ModeHotkeyListener:
    """Captures Shift+Tab while the REPL is busy (streaming / tool calls).

    During ``start()`` ... ``stop()`` fd 0 runs in cbreak mode and a daemon
    thread polls it. A Shift+Tab fires ``on_toggle()``; any other typed bytes
    are collected into a ``TypeAheadBuffer``. ``pause()``/``resume()`` park the
    reader while the engine's own ``sys.stdin.readline()`` prompts are active
    so the user's answers reach the prompt instead of being swallowed.
    ``drain_typeahead()`` returns the collected text for replay.

    All public methods are safe no-ops when there is no tty to watch.
    """

    def __init__(self, on_toggle):
        self._on_toggle = on_toggle
        self._fd = None
        self._saved_termios = None
        self._cbreak_termios = None
        self._thread = None
        self._stop_flag = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()
        self._parser = EscapeSequenceParser()
        self._typeahead = TypeAheadBuffer()
        self._started = False
        self._available = self._check_available()

    @staticmethod
    def _check_available() -> bool:
        if termios is None:
            return False
        try:
            fd = sys.stdin.fileno()
            return os.isatty(fd)
        except (OSError, ValueError, AttributeError):
            return False

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if not self._available or self._started:
            return
        fd = sys.stdin.fileno()
        self._fd = fd
        self._saved_termios = termios.tcgetattr(fd)
        attrs = list(self._saved_termios)
        attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ECHONL)
        attrs[3] |= termios.ISIG
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        self._cbreak_termios = attrs
        self._apply(self._cbreak_termios)
        self._parser.reset()
        self._typeahead.drain()
        self._stop_flag.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._started = True

    def stop(self) -> None:
        if not self._available or not self._started:
            return
        self._stop_flag.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=0.5)
        with self._lock:
            self._restore()
        self._started = False
        self._fd = None

    def pause(self) -> None:
        """Stop watching stdin and restore canonical mode.

        Call before the engine blocks on ``sys.stdin.readline()`` so the
        user's answer goes to the prompt instead of being swallowed.
        """
        if not self._available or not self._started:
            return
        with self._lock:
            self._paused.set()
            self._restore()

    def resume(self) -> None:
        if not self._available or not self._started:
            return
        with self._lock:
            self._paused.clear()
            self._apply(self._cbreak_termios)

    # -- type-ahead ----------------------------------------------------------

    def drain_typeahead(self) -> str:
        if not self._available:
            return ""
        with self._lock:
            return self._typeahead.drain()

    # -- internals -----------------------------------------------------------

    def _apply(self, attrs) -> None:
        if termios is not None and self._fd is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
            except (OSError, termios.error):
                pass

    def _restore(self) -> None:
        if (
            termios is not None
            and self._fd is not None
            and self._saved_termios is not None
        ):
            try:
                termios.tcsetattr(self._fd, termios.TCSANOW, self._saved_termios)
            except (OSError, termios.error):
                pass

    def _run(self) -> None:
        while not self._stop_flag.is_set():
            if self._paused.is_set():
                time.sleep(0.02)
                continue
            with self._lock:
                if self._paused.is_set():
                    continue
                try:
                    ready, _, _ = select.select([self._fd], [], [], 0.05)
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
                try:
                    data = os.read(self._fd, 64)
                except OSError:
                    break
                if not data:
                    break
                self._feed(data)
            time.sleep(0.005)

    def _feed(self, data: bytes) -> None:
        for byte in data:
            matched = self._parser.feed(byte)
            self._typeahead.feed(bytes([byte]))
            if matched:
                self._on_toggle()
