"""The error catalogue (src/app/errors.py): raw errors from the pipeline, the backends and the downloader become
one plain sentence and the button that fixes it. Also the assistant's name in spoken approvals."""
import subprocess
import unittest
from unittest import mock

from src.app import errors, setup


class ExplainTests(unittest.TestCase):
    def code(self, stage, detail, name="Nova"):
        info = errors.explain(stage, detail, name)
        return info and (info["code"], info["fix"]["action"] if info["fix"] else None)

    def test_microphone(self):
        self.assertEqual(self.code("voice", "Voice input unavailable: Error opening InputStream: Unanticipated host error [PaErrorCode -9999]"),
                         ("mic_blocked", "open_mic_privacy"))
        self.assertIn("Nova can't use the microphone", errors.explain("voice", "Unanticipated host error", "Nova")["message"])
        self.assertEqual(self.code("voice", "Error querying device -1"), ("mic_missing", "settings"))
        self.assertEqual(self.code("audio", "Microphone read failed: stream closed"), ("mic_missing", "settings"))
        self.assertEqual(self.code("voice", "Unable to open file 'model.bin' in model 'small'"), ("stt_missing", "settings"))
        self.assertEqual(self.code("voice", "something odd"), ("voice_off", "settings"))

    def test_claude(self):
        self.assertEqual(self.code("llm", "Claude Code CLI not found: claude — install it and log in"), ("claude_missing", "claude_install"))
        self.assertEqual(self.code("llm", "Claude Code error (HTTP 401): Invalid API key · Please run /login"), ("signed_out", "claude_login"))
        self.assertEqual(self.code("llm", "Could not start Claude Code CLI: exit 1. Is `claude` installed and logged in?"),
                         ("signed_out", "claude_login"))
        limit = errors.explain("llm", "Claude Code error: Claude AI usage limit reached ∙ resets 3pm (Asia/Kolkata)")
        self.assertEqual((limit["code"], limit["fix"]["arg"]), ("limit", "ai"))
        self.assertIn("until 3pm (Asia/Kolkata)", limit["message"])

    def test_api_providers_and_network(self):
        self.assertEqual(self.code("llm", "OpenAI (API key): API key rejected: Incorrect API key provided"), ("bad_key", "settings"))
        self.assertEqual(self.code("llm", "Ollama (on this PC): Could not connect to http://127.0.0.1:11434/v1: Connection error."),
                         ("offline", "restart"))
        self.assertEqual(self.code("config", "OpenAI (API key): no API key saved — add it in Settings › AI"), ("ai_setup", "settings"))
        self.assertEqual(self.code("budget", "Daily AI budget of $1.00 reached"), ("budget", "settings"))
        self.assertEqual(self.code("tts", "TTS unavailable: Piper model not found"), ("voice_unavailable", "settings"))

    def test_unknown_errors_keep_their_own_text(self):
        self.assertIsNone(errors.explain("llm", "Something nobody has seen before"))
        self.assertIsNone(errors.explain("task", "Unknown task action 'x'"))


class SpokenApprovalTests(unittest.TestCase):
    def test_the_name_is_in_the_spoken_question(self):
        from src.tools.approvals import ApprovalBroker
        from src.tools.policy import PolicyExecutor
        from src.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec
        reg = ToolRegistry()
        reg.register(ToolSpec("forget", "Delete a memory", {"type": "object", "properties": {}},
                              lambda a, c: ToolResult(ok=True, content="ok"), permission="ask",
                              purpose=lambda a: "Delete memory #3"))
        asked = []
        broker = ApprovalBroker()
        broker.prompter = lambda req: asked.append(req) or broker.resolve(req.id, False, via="test")
        cfg = {"assistant": {"name": "Nova"}, "tools": {"permissions": {}, "approval_timeout_ms": 1000}}
        PolicyExecutor(reg, lambda: cfg, broker, None).execute("forget", {}, ToolContext())
        self.assertEqual(asked[0].spoken, "Nova wants to delete memory #3. Allow?")
        self.assertEqual(asked[0].title, "Allow: Delete memory #3?")


class ClaudeInstallTests(unittest.TestCase):
    def test_runs_the_official_installer_hidden(self):
        with mock.patch("src.core.winproc.run_hidden", return_value=subprocess.CompletedProcess([], 0, "ok", "")) as run, \
                mock.patch.object(setup, "find_claude", return_value="C:/Users/x/.local/bin/claude.exe"):
            self.assertEqual(setup.install_claude_code(), {"ok": True, "cli": "C:/Users/x/.local/bin/claude.exe"})
        self.assertIn("irm https://claude.ai/install.ps1 | iex", run.call_args[0][0])
        with mock.patch("src.core.winproc.run_hidden", return_value=subprocess.CompletedProcess([], 1, "", "blocked\nby policy")), \
                mock.patch.object(setup, "find_claude", return_value=None):
            r = setup.install_claude_code()
        self.assertFalse(r["ok"])
        self.assertIn("by policy", r["error"])


if __name__ == "__main__":
    unittest.main()
