"""Chrome for Jarvis: the user's real Chrome profiles, plus background page reading.

- open_chrome launches the installed Chrome with --profile-directory, so pages open in
  the profile the user names ("Marina", "SPARK IT", an email address ...) with that
  profile's logins. Profiles are read from Chrome's "Local State" file.
- Chrome refuses DevTools automation of the real profiles (Chrome 136+), so clicking and
  typing inside pages goes through desktop_tools (UI Automation + real mouse/keyboard).
- read_webpage / search_web fetch public pages over plain HTTP in the background (no
  window, no cookies, no logins) and return their text as untrusted data.
"""
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from src.core import winproc
from src.core.redact import redact
from src.tools.registry import ToolResult, ToolSpec

DEFAULT_USER_DATA = Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"))
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/155.0.0.0 Safari/537.36")
MAX_BYTES = 3_000_000


# ------------------------------------------------------------------ profiles

def chrome_settings(config: dict) -> dict:
    return config.get("chrome", {}) or {}


def user_data_dir(config: dict) -> Path:
    custom = chrome_settings(config).get("user_data_dir")
    return Path(os.path.expandvars(custom)) if custom else DEFAULT_USER_DATA


def list_profiles(config: dict) -> tuple:
    """([{dir, name, account, email}], last_used_dir) from Chrome's Local State."""
    try:
        state = json.loads((user_data_dir(config) / "Local State").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], ""
    info = (state.get("profile") or {}).get("info_cache") or {}
    profiles = [{"dir": d, "name": v.get("name", ""), "account": v.get("gaia_name", ""),
                 "email": v.get("user_name", "")} for d, v in info.items()]
    profiles.sort(key=lambda p: (p["dir"] != "Default", p["name"].lower()))
    return profiles, (state.get("profile") or {}).get("last_used", "")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9@.]+", " ", (s or "").lower()).strip()


def resolve_profile(query: str, profiles: list) -> tuple:
    """(profile, error). Matches the profile folder, display name, account name or email."""
    q = _norm(query)
    if q in ("default", "main", "my profile", "personal"):
        q = "default"
    fields = lambda p: [_norm(p["dir"]), _norm(p["name"]), _norm(p["account"]), _norm(p["email"]),
                        _norm(p["email"].split("@")[0])]
    for test in (lambda f: f == q, lambda f: f.startswith(q) or q.startswith(f + " "), lambda f: q in f):
        hits = [p for p in profiles if any(f and test(f) for f in fields(p))]
        if len(hits) == 1:
            return hits[0], ""
        if len(hits) > 1:
            return None, f"'{query}' matches several Chrome profiles: " + "; ".join(describe(p) for p in hits)
    return None, f"No Chrome profile matches '{query}'. Profiles: " + "; ".join(describe(p) for p in profiles)


def describe(p: dict) -> str:
    extra = ", ".join(x for x in (p["account"], p["email"]) if x and x != p["name"])
    return f"{p['name']} [{p['dir']}]" + (f" ({extra})" if extra else "")


# ------------------------------------------------------------------ page reading

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "header", "footer", "h1", "h2", "h3", "h4",
             "h5", "h6", "ul", "ol", "table", "main", "nav", "aside", "blockquote", "pre", "dd", "dt"}

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts, self.links, self.headings = [], [], []
        self.title, self._skip, self._in_title, self._heading, self._link = "", 0, False, None, None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3"):
            self._heading = [tag, ""]
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self._link = [urllib.parse.urljoin(self.base_url, href), ""]

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")
        if self._heading and tag == self._heading[0]:
            text = " ".join(self._heading[1].split())
            if text:
                self.headings.append(f"{tag}: {text[:120]}")
            self._heading = None
        if tag == "a" and self._link:
            text = " ".join(self._link[1].split())
            if text:
                self.links.append((text[:80], self._link[0]))
            self._link = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.parts.append(data)
        if self._heading:
            self._heading[1] += data
        if self._link:
            self._link[1] += data

    def text(self) -> str:
        lines = [" ".join(l.split()) for l in "".join(self.parts).splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fetch(url: str, timeout: float = 20) -> tuple:
    """(final_url, content_type, body_text). Raises urllib errors."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9",
                                               "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES)
        ctype = resp.headers.get("Content-Type", "")
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), ctype, raw.decode(charset, errors="replace")


def page_text(url: str, body: str, ctype: str) -> dict:
    if "html" not in ctype.lower() and not body.lstrip().startswith("<"):
        return {"title": "", "url": url, "headings": [], "links": [], "text": body}
    p = _TextExtractor(url)
    p.feed(body)
    return {"title": " ".join(p.title.split()), "url": url, "headings": p.headings[:20], "links": p.links,
            "text": p.text()}


def _ddg_results(body: str) -> list:
    out = []
    strip = lambda s: " ".join(html.unescape(re.sub(r"<[^>]+>", "", s or "")).split())
    for chunk in re.split(r'<a[^>]+class="result__a"', body)[1:]:
        m = re.match(r'[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
        if not m:
            continue
        href = html.unescape(m.group(1))
        target = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if "duckduckgo.com/y.js" in target:            # ads
            continue
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', chunk, re.S)
        out.append({"title": strip(m.group(2)), "url": target, "snippet": strip(snippet.group(1) if snippet else "")})
    return out


# ------------------------------------------------------------------ tools

def register_chrome_tools(reg, profile: dict, config: dict) -> None:
    chrome_path = (profile.get("apps", {}).get("chrome", {}) or {}).get(
        "path", r"C:\Program Files\Google\Chrome\Application\chrome.exe")

    def blocked(url: str) -> str:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        for d in (d.lower() for d in chrome_settings(config).get("blocked_domains", [])):
            if host == d or host.endswith("." + d):
                return host
        return ""

    def normalize_url(u: str) -> str:
        u = u.strip()
        if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
            u = "https://" + u
        if not re.match(r"^https?://", u, re.I):
            raise ValueError(f"Only http(s) URLs can be opened: {u}")
        if blocked(u):
            raise ValueError(f"{blocked(u)} is blocked in settings (chrome.blocked_domains)")
        return u

    def pick_profile(name: str) -> tuple:
        profiles, last_used = list_profiles(config)
        if not profiles:
            return None, f"No Chrome profiles found in {user_data_dir(config)}"
        name = (name or "").strip() or chrome_settings(config).get("default_profile", "")
        if not name:
            p = next((p for p in profiles if p["dir"] == last_used), None) or profiles[0]
            return p, ""
        return resolve_profile(name, profiles)

    def open_chrome(a, c):
        try:
            urls = [normalize_url(u) for u in a.get("urls", [])]
        except ValueError as e:
            return ToolResult(ok=False, status="invalid", content=str(e))
        if a.get("search"):
            urls.append("https://www.google.com/search?q=" + urllib.parse.quote_plus(a["search"]))
        p, err = pick_profile(a.get("profile", ""))
        if p is None:
            return ToolResult(ok=False, status="failed", content=err)
        argv = [chrome_path, f"--profile-directory={p['dir']}"]
        if a.get("new_window"):
            argv.append("--new-window")
        argv += urls
        what = (", ".join(urls) if urls else "a new window") + f" in Chrome profile {describe(p)}"
        if config.get("dry_run"):
            return ToolResult(ok=True, status="dry_run", dry_run=True, content=f"Would open {what}")
        try:
            winproc.launch_program(argv)
        except Exception as e:
            return ToolResult(ok=False, status="failed", content=f"Could not start Chrome: {e}")
        return ToolResult(ok=True, content=f"Opened {what}.", data={"profile": p["dir"], "urls": urls})

    def list_chrome_profiles(a, c):
        profiles, last_used = list_profiles(config)
        if not profiles:
            return ToolResult(ok=False, status="failed", content=f"No Chrome profiles found in {user_data_dir(config)}")
        lines = [describe(p) + (" — last used" if p["dir"] == last_used else "") for p in profiles]
        return ToolResult(ok=True, content="Chrome profiles:\n" + "\n".join(lines), data={"profiles": profiles})

    def read_webpage(a, c):
        try:
            url = normalize_url(a["url"])
        except ValueError as e:
            return ToolResult(ok=False, status="invalid", content=str(e))
        try:
            final, ctype, body = fetch(url)
        except urllib.error.HTTPError as e:
            return ToolResult(ok=False, status="failed", content=f"HTTP {e.code} from {url}", transient=e.code >= 500)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return ToolResult(ok=False, status="failed", content=f"Could not reach {url}: {e}", transient=True)
        if blocked(final):
            return ToolResult(ok=False, status="invalid", content=f"{url} redirected to blocked {blocked(final)}")
        data = page_text(final, body, ctype)
        limit = int(a.get("max_chars", 8000))
        lines = [f"Page: {data['title'] or '(no title)'} — {data['url']}"]
        if data["headings"]:
            lines.append("Headings: " + " | ".join(data["headings"]))
        text = data["text"]
        lines.append("Text:" + (f" (first {limit} of {len(text)} characters)" if len(text) > limit else ""))
        lines.append(text[:limit])
        if a.get("links"):
            lines.append("Links:")
            lines += [f"- {t} -> {u}" for t, u in data["links"][:60]]
        if len(text) < 200:
            lines.append("(Little text: the page may need JavaScript or a login. Offer to open it in Chrome instead.)")
        return ToolResult(ok=True, untrusted=True, content=redact("\n".join(lines)), data={"url": data["url"]})

    def search_web(a, c):
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(a["query"])
        try:
            _, _, body = fetch(url)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return ToolResult(ok=False, status="failed", content=f"Search failed: {e}", transient=True)
        results = [r for r in _ddg_results(body) if not blocked(r["url"])][:int(a.get("max_results", 8))]
        if not results:
            return ToolResult(ok=False, status="failed", transient=True, content="The search returned no results.")
        lines = [f"Search results for '{a['query']}':"]
        lines += [f"{i}. {r['title']} — {r['url']}\n   {r['snippet']}" for i, r in enumerate(results, 1)]
        return ToolResult(ok=True, untrusted=True, content=redact("\n".join(lines)), data={"results": results})

    names = "; ".join(describe(p) for p in list_profiles(config)[0])
    reg.register(ToolSpec(
        name="open_chrome",
        description="Open the user's real Google Chrome in one of their profiles, optionally with URLs or a Google "
                    "search. profile: the name, account name or email the user says (e.g. 'Marina', 'SPARK IT', "
                    "'alex@example.com'); empty = last used. "
                    + (f"Profiles: {names}. " if names else "") +
                    "To click or type inside the page afterwards, use desktop_snapshot / desktop_click / desktop_type.",
        input_schema={"type": "object", "properties": {
            "profile": {"type": "string", "maxLength": 120},
            "urls": {"type": "array", "items": {"type": "string", "minLength": 3, "maxLength": 2000}, "maxItems": 10},
            "search": {"type": "string", "minLength": 1, "maxLength": 500},
            "new_window": {"type": "boolean"}}},
        handler=open_chrome, idempotent=False, timeout_s=20,
        purpose=lambda a: f"Open Chrome ({a.get('profile') or 'last used profile'})"))
    reg.register(ToolSpec(
        name="list_chrome_profiles", description="List the user's Chrome profiles (name, folder, account, email).",
        input_schema={"type": "object", "properties": {}},
        handler=list_chrome_profiles, category="read", purpose=lambda a: "List Chrome profiles"))
    reg.register(ToolSpec(
        name="read_webpage",
        description="Fetch a public web page in the background (no browser window, no logins) and return its title, "
                    "headings and text. links=true also lists the page's links. Use to answer questions from the web.",
        input_schema={"type": "object", "properties": {
            "url": {"type": "string", "minLength": 3, "maxLength": 2000},
            "links": {"type": "boolean"},
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 30000}}, "required": ["url"]},
        handler=read_webpage, category="read", retries=1, timeout_s=40, purpose=lambda a: f"Read {a.get('url')}"))
    reg.register(ToolSpec(
        name="search_web",
        description="Search the web in the background and return result titles, URLs and snippets (then read_webpage "
                    "for details). For facts that change: prices, news, schedules, docs.",
        input_schema={"type": "object", "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"]},
        handler=search_web, category="read", retries=1, timeout_s=40, purpose=lambda a: f"Search: {a.get('query')}"))
