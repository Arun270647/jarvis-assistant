"""Personalization: the user's project rules, email, Chrome profiles and page reading, profile-aware prompt.

The user is the fake "Alex" from tests/fixtures (tests/__init__.py makes it the profile; its project
folders are created in a temp dir). Email goes to a local fake SMTP server (nothing leaves the machine).
Chrome tests use a fake Local State and a stubbed launcher; page reading uses a local HTTP server.
"""
import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from src.core.config_store import load_profile
from src.core.workspace import WorkspaceError, WorkspacePolicy
from src.tools import chrome_tools, email_tools
from src.tools.approvals import ApprovalBroker
from src.tools.policy import PolicyExecutor
from src.tools.registry import ToolContext, ToolRegistry

PROFILE = load_profile()


class FakeSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self):
        self.messages = []

        class Handler(socketserver.StreamRequestHandler):
            def handle(h):
                h.wfile.write(b"220 fake ESMTP\r\n")
                rcpt, data_mode, lines = [], False, []
                while True:
                    line = h.rfile.readline()
                    if not line:
                        return
                    if data_mode:
                        if line in (b".\r\n", b".\n"):
                            data_mode = False
                            self.messages.append({"rcpt": rcpt[:], "data": b"".join(lines).decode("utf-8", "replace")})
                            rcpt, lines = [], []
                            h.wfile.write(b"250 queued\r\n")
                        else:
                            lines.append(line)
                        continue
                    cmd = line.decode().strip().upper()
                    if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                        h.wfile.write(b"250-fake\r\n250 OK\r\n")
                    elif cmd.startswith("MAIL FROM"):
                        h.wfile.write(b"250 OK\r\n")
                    elif cmd.startswith("RCPT TO"):
                        rcpt.append(line.decode().split(":", 1)[1].strip().strip("<>"))
                        h.wfile.write(b"250 OK\r\n")
                    elif cmd == "DATA":
                        data_mode = True
                        h.wfile.write(b"354 go\r\n")
                    elif cmd == "QUIT":
                        h.wfile.write(b"221 bye\r\n")
                        return
                    else:
                        h.wfile.write(b"250 OK\r\n")
        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


class ProjectRuleTests(unittest.TestCase):
    def setUp(self):
        self.ws = WorkspacePolicy(PROFILE, {"agent": {}})

    def test_all_projects_resolve(self):
        self.assertEqual(sorted(self.ws.projects), ["handbook", "landing", "shop"])
        for name, root in self.ws.projects.items():
            self.assertTrue(root.exists(), f"{name}: {root}")

    def test_users_own_rules_are_enforced(self):
        shop = self.ws.projects["shop"]
        self.assertEqual(self.ws.classify_command(["docker", "compose", "up", "-d"], shop), "deny")
        self.assertEqual(self.ws.classify_command(["docker", "ps"], shop), "deny")
        self.assertEqual(self.ws.classify_command(["pytest"], shop), "allow")
        with self.assertRaises(WorkspaceError):
            self.ws.check_writable(shop / "state" / "queue.json")              # protected folder
        with self.assertRaises(WorkspaceError):
            self.ws.check_writable(shop / "ml" / "models" / "ridge.pkl")       # protected pattern
        self.ws.check_writable(shop / "web" / "src" / "App.tsx")
        with self.assertRaises(WorkspaceError):
            self.ws.check_writable(self.ws.projects["handbook"] / "Handbook.pdf")   # read-only project

    def test_everyday_git_and_dev_workflow(self):
        shop = self.ws.projects["shop"]
        for ok in (["git", "add", "-A"], ["git", "commit", "-m", "x"], ["npm", "run", "check"], ["npm", "run", "dev"]):
            self.assertEqual(self.ws.classify_command(ok, shop), "allow", ok)
        for ask in (["git", "push"], ["git", "push", "origin", "main"], ["npm", "install"], ["git", "pull"]):
            self.assertEqual(self.ws.classify_command(ask, shop), "ask", ask)
        for deny in (["git", "push", "--force"], ["git", "reset", "--hard"], ["git", "clean", "-fd"]):
            self.assertEqual(self.ws.classify_command(deny, shop), "deny", deny)

    def test_commits_only_on_the_projects_branch(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        from src.core.winproc import run_hidden
        repo = self.ws.projects["landing"]
        shutil.rmtree(repo / ".git", ignore_errors=True)

        def git(*args):
            r = run_hidden(["git", "-c", "user.name=Alex", "-c", "user.email=alex@example.com", *args],
                           cwd=str(repo), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
        git("init", "-b", "alex/landing")
        git("commit", "--allow-empty", "-m", "start")
        self.assertIsNone(self.ws.branch_problem(["git", "commit", "-m", "x"], repo))
        git("switch", "-c", "experiment")
        self.assertIn("alex/landing", self.ws.branch_problem(["git", "commit", "-m", "x"], repo))
        self.assertIsNone(self.ws.branch_problem(["git", "status"], repo))

    def test_secret_files_in_projects_stay_closed(self):
        with self.assertRaises(WorkspaceError):
            self.ws.resolve(".env", "shop")

    def test_prompt_knows_the_user(self):
        from src.agent.prompt import build_agent_system_prompt
        s = build_agent_system_prompt(["send_email"], PROFILE)
        self.assertIn("Alex Morgan", s)
        self.assertIn("shop: Online shop", s)
        self.assertIn("Never git push", s)


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.smtp = FakeSMTP()
        self.cfg = {"email": {"enabled": True, "preset": "custom", "smtp_host": "127.0.0.1",
                              "smtp_port": self.smtp.server_address[1], "security": "none",
                              "username": "alex@example.com", "from_address": "alex@example.com",
                              "from_name": "Alex Morgan", "trusted_recipients": ["team@example.com"],
                              "signature": "Alex"},
                    "tools": {"permissions": {}, "approval_timeout_ms": 1000}}
        self.reg = ToolRegistry()
        email_tools.register_email_tools(self.reg, PROFILE, self.cfg)
        self.asked = []
        self.broker = ApprovalBroker()
        self.broker.prompter = lambda req: (self.asked.append(req.rows), self.broker.resolve(req.id, True))
        self.ex = PolicyExecutor(self.reg, lambda: self.cfg, self.broker, None)
        self._orig = email_tools.get_password
        email_tools.get_password = lambda user: "test-password"

    def tearDown(self):
        email_tools.get_password = self._orig
        self.smtp.shutdown()
        self.smtp.server_close()

    def test_send_to_self_by_contact_name_needs_no_approval(self):
        r = self.ex.execute("send_email", {"to": ["me"], "subject": "Hi", "body": "Testing"}, ToolContext())
        self.assertTrue(r.ok, r.content)
        self.assertEqual(self.asked, [])
        msg = self.smtp.messages[-1]
        self.assertEqual(msg["rcpt"], ["alex@example.com"])
        self.assertIn("Subject: Hi", msg["data"])
        self.assertIn("Alex Morgan", msg["data"])
        self.assertIn("Alex", msg["data"].split("Testing", 1)[1])     # signature

    def test_other_recipients_need_approval_showing_the_mail(self):
        r = self.ex.execute("send_email", {"to": ["acme"], "cc": ["team@example.com"], "subject": "Enquiry",
                                           "body": "Hello"}, ToolContext())
        self.assertTrue(r.ok, r.content)
        self.assertEqual(len(self.asked), 1)
        shown = {row["k"]: row["v"] for row in self.asked[0]}
        self.assertEqual(shown["To"], "info@acme.example")
        self.assertEqual(shown["Subject"], "Enquiry")
        self.assertIn("Hello", shown["Body"])

    def test_denied_mail_is_not_sent(self):
        self.broker.prompter = lambda req: self.broker.resolve(req.id, False)
        r = self.ex.execute("send_email", {"to": ["someone@example.com"], "subject": "x", "body": "y"}, ToolContext())
        self.assertEqual(r.status, "denied")
        self.assertEqual(self.smtp.messages, [])

    def test_unknown_names_are_never_guessed(self):
        r = self.ex.execute("send_email", {"to": ["Jordan"], "subject": "x", "body": "y"}, ToolContext())
        self.assertEqual(r.status, "invalid")
        self.assertIn("never guess", r.content)

    def test_without_a_profile_names_are_refused_but_addresses_work(self):
        reg = ToolRegistry()
        email_tools.register_email_tools(reg, {}, self.cfg)
        ex = PolicyExecutor(reg, lambda: self.cfg, self.broker, None)
        self.assertEqual(ex.execute("send_email", {"to": ["me"], "subject": "x", "body": "y"}, ToolContext()).status,
                         "invalid")
        r = ex.execute("send_email", {"to": ["someone@example.com"], "subject": "Hi", "body": "y"}, ToolContext())
        self.assertTrue(r.ok, r.content)
        self.assertEqual(self.smtp.messages[-1]["rcpt"], ["someone@example.com"])

    def test_duplicate_send_in_a_task_is_refused(self):
        from src.memory.store import MemoryStore
        from tests.fakes import temp_db
        mem = MemoryStore(temp_db())
        try:
            ex = PolicyExecutor(self.reg, lambda: self.cfg, self.broker, mem)
            ctx = ToolContext(task_id=7)
            args = {"to": ["me"], "subject": "Once", "body": "only once"}
            self.assertTrue(ex.execute("send_email", args, ctx).ok)
            self.assertEqual(ex.execute("send_email", args, ctx).status, "duplicate")
            self.assertEqual(len(self.smtp.messages), 1)
        finally:
            mem.close()

    def test_no_password_gives_setup_message(self):
        email_tools.get_password = lambda user: None
        r = self.ex.execute("send_email", {"to": ["me"], "subject": "x", "body": "y"}, ToolContext())
        self.assertFalse(r.ok)
        self.assertIn("Settings > Email", r.content)

    @unittest.skipIf(sys.platform == "darwin" and os.environ.get("CI"), "a CI Mac's Keychain may be locked")
    def test_credential_manager_round_trip(self):
        email_tools.get_password = self._orig
        user = "jarvis-unittest@example.invalid"
        try:
            email_tools.set_password(user, "s3cret-value")
            self.assertEqual(email_tools.get_password(user), "s3cret-value")
            self.assertTrue(email_tools.has_password(user))
        finally:
            email_tools.delete_password(user)
        self.assertIsNone(email_tools.get_password(user))


PAGE = """<!doctype html><html><head><title>Jarvis test shop</title><style>.x{}</style></head><body>
<script>var hidden = 1;</script>
<h1>Order form</h1><p>Blue mug &amp; saucer: Rs 450</p><a href="/cart">Cart</a></body></html>"""

LOCAL_STATE = {"profile": {"last_used": "Profile 1", "info_cache": {
    "Default": {"name": "Alex Morgan", "gaia_name": "Alex M", "user_name": "alex@example.com"},
    "Profile 1": {"name": "Work", "gaia_name": "Work Account", "user_name": "work@example.com"},
    "Profile 24": {"name": "Work Admin", "gaia_name": "", "user_name": ""},
    "Profile 19": {"name": "SIDE PROJECT", "gaia_name": "SIDE PROJECT", "user_name": "side@example.com"}}}}


class ChromeTests(unittest.TestCase):
    """open_chrome uses the user's real profiles; read_webpage fetches pages without a browser."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="jarvis-web-"))
        (cls.dir / "index.html").write_text(PAGE, encoding="utf-8")
        (cls.dir / "Local State").write_text(json.dumps(LOCAL_STATE), encoding="utf-8")
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(cls.dir), **k)
        cls.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/index.html"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.launched = []
        self._launch = chrome_tools.winproc.launch_program
        chrome_tools.winproc.launch_program = lambda argv, **k: self.launched.append(argv)
        self.cfg = {"chrome": {"user_data_dir": str(self.dir), "blocked_domains": ["blocked.example"]},
                    "tools": {"permissions": {}, "approval_timeout_ms": 1000}}
        self.reg = ToolRegistry()
        chrome_tools.register_chrome_tools(self.reg, {"apps": {"chrome": {"path": "chrome.exe"}}}, self.cfg)
        self.ex = PolicyExecutor(self.reg, lambda: self.cfg, ApprovalBroker(), None)

    def tearDown(self):
        chrome_tools.winproc.launch_program = self._launch

    def test_profile_matching(self):
        profiles, _ = chrome_tools.list_profiles(self.cfg)
        pick = lambda q: (chrome_tools.resolve_profile(q, profiles)[0] or {}).get("dir")
        self.assertEqual(pick("Work"), "Profile 1")                    # exact name beats "Work Admin"
        self.assertEqual(pick("work admin"), "Profile 24")
        self.assertEqual(pick("side"), "Profile 19")
        self.assertEqual(pick("alex@example.com"), "Default")
        self.assertEqual(pick("default"), "Default")
        self.assertIsNone(pick("nobody"))

    def test_open_chrome_in_named_profile(self):
        r = self.ex.execute("open_chrome", {"profile": "SIDE PROJECT", "urls": ["mail.google.com"]}, ToolContext())
        self.assertTrue(r.ok, r.content)
        self.assertEqual(self.launched, [["chrome.exe", "--profile-directory=Profile 19", "https://mail.google.com"]])
        r = self.ex.execute("open_chrome", {"search": "lofi beats"}, ToolContext())
        self.assertTrue(r.ok, r.content)
        self.assertEqual(self.launched[-1][1], "--profile-directory=Profile 1")   # last used
        self.assertIn("q=lofi+beats", self.launched[-1][2])

    def test_open_chrome_refuses_bad_urls_and_unknown_profiles(self):
        for args in ({"urls": ["file:///C:/Windows/win.ini"]}, {"urls": ["https://blocked.example/x"]},
                     {"profile": "nobody"}):
            self.assertFalse(self.ex.execute("open_chrome", args, ToolContext()).ok, args)
        self.assertEqual(self.launched, [])

    def test_read_webpage(self):
        r = self.ex.execute("read_webpage", {"url": self.url, "links": True}, ToolContext())
        self.assertTrue(r.ok, r.content)
        self.assertIn("Jarvis test shop", r.content)
        self.assertIn("Blue mug & saucer: Rs 450", r.content)
        self.assertIn("h1: Order form", r.content)
        self.assertIn("Cart -> http://127.0.0.1", r.content)
        self.assertNotIn("hidden", r.content)                         # scripts are not page text
        self.assertIn("<untrusted_output>", r.as_text())
        self.assertFalse(self.ex.execute("read_webpage", {"url": "https://blocked.example"}, ToolContext()).ok)

    def test_duckduckgo_result_parsing(self):
        body = ('<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg='
                'https%3A%2F%2Fexample.com%2Fa&amp;rut=x">Example <b>A</b></a>'
                '<a class="result__snippet" href="#">Snippet &amp; more</a></div>')
        self.assertEqual(chrome_tools._ddg_results(body),
                         [{"title": "Example A", "url": "https://example.com/a", "snippet": "Snippet & more"}])


if __name__ == "__main__":
    unittest.main()
