"""Background downloads of catalogue models (config/models.json).

- One download at a time, in the order asked, on a worker thread, so the setup wizard keeps working.
- Resumable: data goes to <file>.part; after an interruption or a restart the next attempt asks the server
  for the rest (HTTP Range) and continues. Network errors are retried a few times.
- Every file is checked against its SHA-256 before it is renamed into place; a mismatch deletes it.
- import_file() takes a file the user already has (slow or offline computers), checks it and copies it in.
- Progress is reported as "download" events: {"type": "download", "option", "name", "state", "done",
  "total", "speed", "eta_s", "file", "error"}; state is queued | downloading | verifying | installing (an
  add-on, src/core/addons.py, after the files) | done | failed |
  cancelled, or partial after importing some of an option's files.
"""
import hashlib
import queue
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from src.core import models

CHUNK = 1 << 16
RETRIES = 3
USER_AGENT = "Jarvis model downloader"


ACTIVE = ("queued", "downloading", "verifying", "installing")

class DownloadError(Exception):
    pass


class Cancelled(Exception):
    pass


def sha256_of(path: Path, h=None) -> "hashlib._Hash":
    h = h or hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h


class Downloader:
    def __init__(self, emit: Optional[Callable[[dict], None]] = None, opener=urllib.request.urlopen,
                 retry_delay: float = 2.0):
        self.emit = emit or (lambda ev: None)
        self._open = opener
        self.retry_delay = retry_delay
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._jobs: dict = {}                   # option id -> job state (the last event's fields)
        self._cancel: dict = {}                 # option id -> threading.Event
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    # ------------------------------------------------------------ public

    def start(self, option_id: str) -> str:
        """Queue an option's files. Returns installed | queued | running | builtin."""
        _, option = models.find(option_id)
        st = models.status(option)
        if st["state"] in ("installed", "builtin"):
            self._update(option, state="done" if st["state"] == "installed" else "builtin",
                         done=st["total"], total=st["total"])
            return st["state"]
        with self._lock:
            job = self._jobs.get(option_id)
            if job and job["state"] in ACTIVE:
                return "running"
            self._cancel[option_id] = threading.Event()
        self._update(option, state="queued", done=st["have"], total=st["total"], error=None)
        self._queue.put(option_id)
        self._ensure_worker()
        return "queued"

    def cancel(self, option_id: str) -> None:
        with self._lock:
            ev = self._cancel.get(option_id)
        if ev is not None:
            ev.set()

    def jobs(self) -> list:
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def busy(self) -> bool:
        return any(j["state"] in ACTIVE for j in self.jobs())

    def import_file(self, option_id: str, file_name: str, source: str) -> dict:
        """Use a file the user downloaded themselves: checked against the catalogue, then copied in."""
        _, option = models.find(option_id)
        entry = next((f for f in option.get("files", []) if f["name"] == file_name), None)
        if entry is None:
            return {"ok": False, "error": f"{option['name']} has no file called {file_name}"}
        src = Path(source)
        if not src.is_file():
            return {"ok": False, "error": "That file doesn't exist."}
        if src.stat().st_size != entry["size"] or sha256_of(src).hexdigest() != entry["sha256"]:
            return {"ok": False, "error": f"That isn't the right file: its contents don't match {file_name}."}
        dest = models.target(option, entry)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, part)
        part.replace(dest)
        st = models.status(option)
        self._update(option, state="done" if st["state"] == "installed" else "partial", done=st["have"],
                     total=st["total"], file=file_name, error=None)
        return {"ok": True, "state": st["state"]}

    # ------------------------------------------------------------ worker

    def _ensure_worker(self):
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="model-downloads", daemon=True)
                self._worker.start()

    def _run(self):
        while True:
            try:
                option_id = self._queue.get(timeout=5)
            except queue.Empty:
                return
            _, option = models.find(option_id)
            cancel = self._cancel.get(option_id) or threading.Event()
            try:
                self._download_option(option, cancel)
                st = models.status(option)
                self._update(option, state="done", done=st["total"], total=st["total"], speed=0, eta_s=0)
            except Cancelled:
                self._update(option, state="cancelled")
            except Exception as e:
                self._update(option, state="failed", error=str(e) or type(e).__name__, speed=0, eta_s=None)

    def _download_option(self, option: dict, cancel: threading.Event):
        files = option.get("files") or []
        total = sum(f["size"] for f in files)
        done_before = 0
        for f in files:
            dest = models.target(option, f)
            if dest.exists() and dest.stat().st_size == f["size"]:
                done_before += f["size"]
                continue
            for attempt in range(RETRIES + 1):
                try:
                    self._download_file(option, f, dest, done_before, total, cancel)
                    break
                except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                    if isinstance(e, urllib.error.HTTPError) and e.code in (403, 404, 410):
                        raise DownloadError(f"The server no longer offers {f['name']} (HTTP {e.code}).") from e
                    if attempt == RETRIES:
                        raise DownloadError(f"The download of {f['name']} keeps failing: {e}") from e
                    self._update(option, state="downloading", error=f"Connection problem, retrying ({attempt + 1})")
                    if cancel.wait(self.retry_delay * (attempt + 1)):
                        raise Cancelled()
            done_before += f["size"]
        addon = option.get("addon")
        if addon:
            from src.core import addons
            if not addons.installed(addon):
                if cancel.is_set():
                    raise Cancelled()
                self._update(option, state="installing", file=addons.spec(addon)["name"], speed=0, eta_s=None)
                try:
                    addons.install(addon)
                except RuntimeError as e:
                    raise DownloadError(str(e)) from e

    def _download_file(self, option: dict, f: dict, dest: Path, done_before: int, total: int,
                       cancel: threading.Event):
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        if have > f["size"]:
            part.unlink()
            have = 0
        headers = {"User-Agent": USER_AGENT}
        if 0 < have < f["size"]:
            headers["Range"] = f"bytes={have}-"
        h = hashlib.sha256()
        if have < f["size"]:
            response = self._open(urllib.request.Request(f["url"], headers=headers), timeout=60)
            with response:
                if have and getattr(response, "status", 200) != 206:
                    have = 0                         # the server ignored the range: start over
                if have:
                    sha256_of(part, h)
                mode = "ab" if have else "wb"
                started, last, got = time.monotonic(), 0.0, 0
                with open(part, mode) as out:
                    while True:
                        if cancel.is_set():
                            raise Cancelled()
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        h.update(chunk)
                        got += len(chunk)
                        now = time.monotonic()
                        if now - last >= 0.25:
                            last = now
                            speed = got / max(now - started, 1e-6)
                            done = done_before + have + got
                            self._update(option, state="downloading", file=f["name"], done=done, total=total,
                                         speed=round(speed), eta_s=round((total - done) / speed) if speed else None,
                                         error=None)
        else:
            sha256_of(part, h)
        size = part.stat().st_size
        if size < f["size"]:
            raise ConnectionError(f"the connection closed early ({size} of {f['size']} bytes)")
        self._update(option, state="verifying", file=f["name"], done=done_before + size, total=total)
        if size != f["size"] or h.hexdigest() != f["sha256"]:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{f['name']} didn't match its checksum, so it was deleted. Try again.")
        part.replace(dest)

    # ------------------------------------------------------------ events

    def _update(self, option: dict, **fields):
        with self._lock:
            job = self._jobs.setdefault(option["id"], {"type": "download", "option": option["id"], "name": option["name"],
                                                       "state": "queued", "done": 0, "total": 0, "speed": 0,
                                                       "eta_s": None, "file": None, "error": None})
            job.update(fields)
            ev = dict(job)
        try:
            self.emit(ev)
        except Exception:
            pass
