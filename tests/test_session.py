"""Tests for chat-mode session persistence (auto-save, resume, /sessions).

Covers the shared serialize/apply round-trip, encoded-cwd grouping, the
auto-save helper (writes/updates/skips-empty/disabled), most-recent session
selection, cross-project resume with re-association, /new archiving, and
the XDG-aware sessions directory resolution.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

current_file = os.path.abspath(__file__)
lama_ole_dir = os.path.abspath(os.path.join(os.path.dirname(current_file), ".."))
if lama_ole_dir not in sys.path:
    sys.path.insert(0, lama_ole_dir)

import chat  # noqa: E402


def _load_cli_module():
    """Load lama_ole.py as a module (the top-level lama_ole/ package shadows it)."""
    path = os.path.join(lama_ole_dir, "lama_ole.py")
    spec = importlib.util.spec_from_file_location("lama_ole_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _load_cli_module()


def _make_state(**kwargs):
    kwargs.setdefault("client", None)
    kwargs.setdefault("model", "m")
    return chat.ChatState(**kwargs)


class SessionSerializeTest(unittest.TestCase):
    def test_encode_cwd(self):
        self.assertEqual(
            chat._encode_cwd("/home/nx/proj"),
            "home-nx-proj-" + hashlib.sha1(b"/home/nx/proj").hexdigest()[:10],
        )
        self.assertEqual(
            chat._encode_cwd("/a b/c"),
            "a-b-c-" + hashlib.sha1(b"/a b/c").hexdigest()[:10],
        )
        self.assertRegex(chat._encode_cwd("/home/nx/proj"), r"^home-nx-proj-[0-9a-f]{10}$")
        self.assertEqual(
            chat._encode_cwd("/home/nx/proj"), chat._encode_cwd("/home/nx/proj")
        )

    def test_encode_cwd_collision_free(self):
        base = "/home/nx/work"
        variants = [
            "/lama_ole",
            "/lama-ole",
            "/lama.ole",
            "/lama ole",
            "/lama..ole",
            "/lama__ole",
        ]
        slugs = {chat._encode_cwd(base + v) for v in variants}
        self.assertEqual(len(slugs), len(variants), slugs)

        self.assertNotEqual(chat._encode_cwd(base + "/café"), chat._encode_cwd(base + "/caf"))
        self.assertNotEqual(
            chat._encode_cwd(base + "/日本語プロジェクト"),
            chat._encode_cwd(base + "/"),
        )
        self.assertNotEqual(chat._encode_cwd("/p/a"), chat._encode_cwd("/p/a_b"))

    def test_replay_history(self):
        state = _make_state()
        state.messages = [
            {"role": "system", "content": "secret system prompt"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there", "thinking": "inner monologue"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "Berlin"}}}
                ],
            },
            {"role": "tool", "content": "[data from ...]", "tool_name": "get_weather"},
            {"role": "assistant", "content": "it is sunny"},
        ]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._replay_history(state, use_color=False)
        out = buf.getvalue()
        self.assertNotIn("secret system prompt", out)
        self.assertNotIn("inner monologue", out)
        self.assertIn(">>> hi\n", out)
        self.assertIn("hello there\n", out)
        self.assertIn("it is sunny\n", out)
        self.assertNotIn("[tool:", out)
        self.assertNotIn("[tool result:", out)

    def test_replay_history_shows_thinking_when_enabled(self):
        state = _make_state(show_thinking=True)
        state.messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "answer", "thinking": "hmm"},
        ]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._replay_history(state, use_color=True)
        out = buf.getvalue()
        self.assertIn("hmm", out)
        self.assertIn("answer", out)
        self.assertIn("\x01\033[", out)

    def test_replay_history_verbose_shows_tool_markers(self):
        state = _make_state(verbose=1)
        state.messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "Berlin"}}}
                ],
            },
            {"role": "tool", "content": "[data from ...]", "tool_name": "get_weather"},
        ]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._replay_history(state, use_color=False)
        out = buf.getvalue()
        self.assertIn("[tool: get_weather(city='Berlin')]", out)
        self.assertIn("[tool result: get_weather]", out)

    def test_replay_history_colored(self):
        state = _make_state()
        state.messages = [{"role": "user", "content": "hi"}]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._replay_history(state, use_color=True)
        self.assertIn("\x01\033[", buf.getvalue())

    def test_new_session_id_unique(self):
        ids = {chat.new_session_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)
        self.assertEqual(len(chat.new_session_id()), 32)

    def test_session_title(self):
        state = _make_state()
        state.messages = [{"role": "user", "content": "hello world"}]
        self.assertEqual(chat._session_title(state), "hello world")
        state.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "a\nb"},
        ]
        self.assertEqual(chat._session_title(state), "a b")
        self.assertEqual(chat._session_title(_make_state()), "")

    def test_serialize_omits_unset_fields(self):
        data = chat.serialize_session(_make_state())
        self.assertEqual(data["model"], "m")
        self.assertNotIn("system_prompt", data)
        self.assertNotIn("skill", data)
        self.assertNotIn("loaded_tool_modules", data)
        self.assertNotIn("session_id", data)
        self.assertNotIn("cwd", data)

    def test_apply_session_ignores_unknown_keys(self):
        state = _make_state()
        chat.apply_session(state, {"messages": [{"role": "user", "content": "hi"}], "future": 1})
        self.assertEqual(state.messages, [{"role": "user", "content": "hi"}])

    def test_round_trip(self):
        state = _make_state()
        state.messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}]
        state.skill = "code-reviewer"
        state.skill_text = "Review."
        state.system_prompt = "SP"
        data = chat.serialize_session(state, session_id="abc", cwd="/x", created_at=1.0)

        state2 = _make_state(model="other")
        chat.apply_session(state2, data)
        self.assertEqual(state2.model, "m")
        self.assertEqual(state2.skill, "code-reviewer")
        self.assertEqual(state2.skill_text, "Review.")
        self.assertEqual(state2.system_prompt, "SP")
        self.assertEqual(state2.session_id, "abc")
        self.assertEqual(state2.session_created_at, 1.0)
        self.assertEqual(state2.messages, state.messages)

    def test_round_trip_preserves_thinking(self):
        state = _make_state()
        state.messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "answer", "thinking": "hmm"},
        ]
        data = chat.serialize_session(state, session_id="abc")
        state2 = _make_state()
        chat.apply_session(state2, data)
        self.assertEqual(state2.messages[1]["thinking"], "hmm")


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self._tmp)
        self.sessions_dir = os.path.join(self._tmp, "sessions")

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _session_path(self, sid):
        return os.path.join(chat.session_dir_for(os.getcwd(), self.sessions_dir), sid + ".json")

    def test_autosave_writes_file(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "hi"}]
        chat.autosave_session(state)
        self.assertTrue(os.path.isfile(self._session_path("s1")))
        with open(self._session_path("s1"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["cwd"], os.getcwd())
        self.assertEqual(data["model"], "m")

    def test_autosave_file_mode_0600(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "hi"}]
        chat.autosave_session(state)
        mode = os.stat(self._session_path("s1")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_autosave_updates_file(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "first"}]
        chat.autosave_session(state)
        state.messages.append({"role": "user", "content": "second"})
        chat.autosave_session(state)
        with open(self._session_path("s1"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data["messages"]), 2)

    def test_autosave_skips_empty(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        chat.autosave_session(state)
        self.assertFalse(os.path.exists(self._session_path("s1")))

    def test_autosave_disabled(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1", session_autosave=False)
        state.messages = [{"role": "user", "content": "hi"}]
        chat.autosave_session(state)
        self.assertFalse(os.path.exists(self._session_path("s1")))

    def test_find_recent_session(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="old")
        state.messages = [{"role": "user", "content": "old"}]
        chat.autosave_session(state)
        path = self._session_path("old")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["updated_at"] = time.time() - 100
        chat._write_session_file(path, data)

        state2 = _make_state(sessions_dir=self.sessions_dir, session_id="new")
        state2.messages = [{"role": "user", "content": "new"}]
        chat.autosave_session(state2)

        res = chat.find_recent_session(self.sessions_dir, os.getcwd())
        self.assertIsNotNone(res)
        self.assertEqual(res[1]["session_id"], "new")

    def test_find_recent_session_ignores_other_cwd(self):
        state = _make_state()
        state.messages = [{"role": "user", "content": "hi"}]
        data = chat.serialize_session(state, session_id="s1", cwd="/elsewhere", created_at=time.time())
        old_dir = chat.session_dir_for("/elsewhere", self.sessions_dir)
        os.makedirs(old_dir, exist_ok=True)
        chat._write_session_file(os.path.join(old_dir, "s1.json"), data)
        self.assertIsNone(chat.find_recent_session(self.sessions_dir, os.getcwd()))

    def test_resume_by_id_reassociates_moved_session(self):
        state = _make_state()
        state.messages = [{"role": "user", "content": "hi"}]
        data = chat.serialize_session(state, session_id="s1", cwd="/old/proj", created_at=time.time())
        old_dir = chat.session_dir_for("/old/proj", self.sessions_dir)
        os.makedirs(old_dir, exist_ok=True)
        old_path = os.path.join(old_dir, "s1.json")
        chat._write_session_file(old_path, data)

        state2 = _make_state(sessions_dir=self.sessions_dir)
        chat._cmd_resume("s1", state2)
        self.assertEqual(state2.session_id, "s1")
        self.assertEqual(state2.messages, [{"role": "user", "content": "hi"}])
        self.assertTrue(os.path.isfile(self._session_path("s1")))
        self.assertFalse(os.path.exists(old_path))

    def test_resume_picker_selects(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "pick me"}]
        chat.autosave_session(state)

        state2 = _make_state(sessions_dir=self.sessions_dir)
        with patch("builtins.input", return_value="1"):
            chat._cmd_resume("", state2)
        self.assertEqual(state2.session_id, "s1")
        self.assertEqual(len(state2.messages), 1)

    def test_resume_no_match(self):
        state2 = _make_state(sessions_dir=self.sessions_dir)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._cmd_resume("nope", state2)
        self.assertIn("No session matching", buf.getvalue())

    def test_sessions_lists(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "hi"}]
        chat.autosave_session(state)
        state2 = _make_state(sessions_dir=self.sessions_dir)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._cmd_sessions("", state2)
        out = buf.getvalue()
        self.assertIn("s1", out)
        self.assertIn("session(s) stored in", out)

    def test_new_archives_and_starts_fresh(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "keep me"}]
        chat.autosave_session(state)
        state.messages.append({"role": "user", "content": "before new"})
        chat._handle_command("/new", state)
        self.assertNotEqual(state.session_id, "s1")
        self.assertEqual(state.messages, [])
        self.assertTrue(os.path.isfile(self._session_path("s1")))

    def test_load_archives_previous_conversation(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "keep me"}]
        snap = os.path.join(self._tmp, "snap.json")
        chat._cmd_save(snap, state)
        state.messages.append({"role": "user", "content": "before load"})

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._cmd_load(snap, state)

        self.assertIn("archived", buf.getvalue())
        self.assertEqual(state.session_id, "s1")
        self.assertEqual(
            [m["content"] for m in state.messages if m["role"] == "user"],
            ["keep me"],
        )
        with open(self._session_path("s1"), encoding="utf-8") as f:
            archived = json.load(f)
        self.assertEqual(
            [m["content"] for m in archived["messages"] if m["role"] == "user"],
            ["keep me", "before load"],
        )

    def test_load_empty_session_does_not_archive(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "snapshot"}]
        snap = os.path.join(self._tmp, "snap.json")
        chat._cmd_save(snap, state)

        fresh = _make_state(sessions_dir=self.sessions_dir, session_id="s2")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._cmd_load(snap, fresh)
        self.assertNotIn("archived", buf.getvalue())
        self.assertFalse(os.path.exists(self._session_path("s2")))

    def test_rename_current_session_persists_title(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "original title"}]
        chat.autosave_session(state)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._cmd_rename("my project", state)
        self.assertIn("Renamed current session to 'my project'", buf.getvalue())

        with open(self._session_path("s1"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["title"], "my project")

        chat.autosave_session(state)
        with open(self._session_path("s1"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["title"], "my project")

    def test_rename_stored_session_by_id_prefix(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "keep me"}]
        chat.autosave_session(state)
        state2 = _make_state(sessions_dir=self.sessions_dir, session_id="s2")
        state2.messages = [{"role": "user", "content": "other"}]
        chat.autosave_session(state2)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._cmd_rename("s1 renamed session", state2)

        with open(self._session_path("s1"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["title"], "renamed session")
        self.assertNotIn("Renamed current", buf.getvalue())

    def test_rename_ambiguous_prefix(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "a"}]
        chat.autosave_session(state)
        state2 = _make_state(sessions_dir=self.sessions_dir, session_id="s2")
        state2.messages = [{"role": "user", "content": "b"}]
        chat.autosave_session(state2)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._cmd_rename("s new", state2)
        self.assertIn("Ambiguous", buf.getvalue())
        with open(self._session_path("s1"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["title"], "a")

    def test_rename_survives_resume_and_sessions_listing(self):
        state = _make_state(sessions_dir=self.sessions_dir, session_id="s1")
        state.messages = [{"role": "user", "content": "derived title"}]
        chat.autosave_session(state)
        chat._cmd_rename("project planning", state)

        resumed = _make_state(sessions_dir=self.sessions_dir)
        chat._cmd_resume("s1", resumed)
        self.assertEqual(resumed.session_title, "project planning")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chat._cmd_sessions("", resumed)
        self.assertIn("project planning", buf.getvalue())


class SessionsDirTest(unittest.TestCase):
    def test_xdg_data_home(self):
        old_xdg = os.environ.get("XDG_DATA_HOME")
        old_dir = os.environ.get("LAMA_OLE_SESSION_DIR")
        os.environ["XDG_DATA_HOME"] = "/xdg"
        os.environ.pop("LAMA_OLE_SESSION_DIR", None)
        try:
            self.assertEqual(CLI._default_sessions_dir(), "/xdg/lama_ole/sessions")
        finally:
            if old_xdg is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_xdg
            if old_dir is None:
                os.environ.pop("LAMA_OLE_SESSION_DIR", None)
            else:
                os.environ["LAMA_OLE_SESSION_DIR"] = old_dir

    def test_env_override(self):
        old_dir = os.environ.get("LAMA_OLE_SESSION_DIR")
        os.environ["LAMA_OLE_SESSION_DIR"] = "/custom/sessions"
        try:
            self.assertEqual(CLI._default_sessions_dir(), "/custom/sessions")
        finally:
            if old_dir is None:
                os.environ.pop("LAMA_OLE_SESSION_DIR", None)
            else:
                os.environ["LAMA_OLE_SESSION_DIR"] = old_dir

    def test_resume_flag_defaults_to_true(self):
        old = os.environ.get("LAMA_OLE_RESUME")
        os.environ.pop("LAMA_OLE_RESUME", None)
        try:
            args = CLI.build_parser().parse_args([])
            self.assertTrue(args.resume)
        finally:
            if old is None:
                os.environ.pop("LAMA_OLE_RESUME", None)
            else:
                os.environ["LAMA_OLE_RESUME"] = old

    def test_autosave_flag_defaults_to_true(self):
        old = os.environ.get("LAMA_OLE_AUTOSAVE")
        os.environ.pop("LAMA_OLE_AUTOSAVE", None)
        try:
            args = CLI.build_parser().parse_args([])
            self.assertTrue(args.autosave)
        finally:
            if old is None:
                os.environ.pop("LAMA_OLE_AUTOSAVE", None)
            else:
                os.environ["LAMA_OLE_AUTOSAVE"] = old

    def test_resume_and_autosave_are_independent(self):
        old_r = os.environ.get("LAMA_OLE_RESUME")
        old_a = os.environ.get("LAMA_OLE_AUTOSAVE")
        os.environ["LAMA_OLE_RESUME"] = "false"
        os.environ["LAMA_OLE_AUTOSAVE"] = "false"
        try:
            args = CLI.build_parser().parse_args([])
            self.assertFalse(args.resume)
            self.assertFalse(args.autosave)
        finally:
            if old_r is None:
                os.environ.pop("LAMA_OLE_RESUME", None)
            else:
                os.environ["LAMA_OLE_RESUME"] = old_r
            if old_a is None:
                os.environ.pop("LAMA_OLE_AUTOSAVE", None)
            else:
                os.environ["LAMA_OLE_AUTOSAVE"] = old_a


if __name__ == "__main__":
    unittest.main()
