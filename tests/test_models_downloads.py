"""Hardware rules, the model catalogue (config/models.json) and the downloader, against a local file server
(no network): resume after a dropped connection, checksum mismatch, import of a file the user already has."""
import hashlib
import re
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from src.core import config_store, downloads, hardware, models, paths


class FileServer:
    """Serves bytes by path; honours Range; can cut the first response short to simulate a dropped connection."""

    def __init__(self, files: dict):
        self.files = files
        self.drop_first_after = {}          # path -> bytes to send before closing, once
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            disable_nagle_algorithm = True

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests.append((self.path, self.headers.get("Range")))
                data = outer.files.get(self.path)
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                start = 0
                m = re.match(r"bytes=(\d+)-", self.headers.get("Range") or "")
                if m:
                    start = int(m.group(1))
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                cut = outer.drop_first_after.pop(self.path, None)
                try:
                    self.wfile.write(body[:cut] if cut is not None else body)
                except (ConnectionError, OSError):
                    pass
                if cut is not None:
                    self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def entry(name, data, url):
    return {"name": name, "url": url, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class HardwareRuleTests(unittest.TestCase):
    def test_rules(self):
        facts = {"nvidia_gpu": True, "nvidia_free_vram_gb": 3.5, "cuda_libs": True, "cpu_logical_cores": 4}
        self.assertTrue(hardware.matches("always", facts))
        self.assertTrue(hardware.matches("nvidia_free_vram_gb >= 3 and cuda_libs", facts))
        self.assertFalse(hardware.matches("nvidia_free_vram_gb >= 4", facts))
        self.assertFalse(hardware.matches("cpu_logical_cores >= 8", facts))
        self.assertFalse(hardware.matches("unknown_fact", facts))
        self.assertFalse(hardware.matches("nvidia_gpu >= 1", facts), "booleans are not numbers")
        self.assertFalse(hardware.matches("__import__('os')", facts), "rules are parsed, never evaluated")

    def test_detect_without_nvidia(self):
        import subprocess
        with mock.patch("src.core.winproc.run_hidden", side_effect=FileNotFoundError("no nvidia-smi")):
            facts = hardware.detect()
        self.assertFalse(facts["nvidia_gpu"])
        self.assertGreaterEqual(facts["cpu_logical_cores"], 1)
        fake = subprocess.CompletedProcess([], 0, "NVIDIA GeForce RTX 4070, 12282, 11000\n", "")
        with mock.patch("src.core.winproc.run_hidden", return_value=fake), \
                mock.patch.object(hardware, "cuda_libs_found", return_value=True):
            facts = hardware.detect()
        self.assertEqual((facts["nvidia_gpu"], facts["gpu_name"], facts["nvidia_vram_gb"], facts["cuda_libs"]),
                         (True, "NVIDIA GeForce RTX 4070", 12.0, True))


class CatalogueTests(unittest.TestCase):
    def test_every_option_is_complete_and_pinned(self):
        for g in models.groups():
            self.assertTrue(g["title"] and g["explain"] and g["recommend"], g["id"])
            ids = {o["id"] for o in g["options"]}
            for rule in g["recommend"]:
                self.assertIn(rule["option"], ids)
            for o in g["options"]:
                self.assertTrue(o["name"] and o["summary"] and o["license"], o["id"])
                for f in o.get("files", []):
                    self.assertTrue(f["url"].startswith("https://"), f["url"])
                    self.assertNotIn("/resolve/main/", f["url"], "pinned to a commit, so the checksum stays valid")
                    self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
                    self.assertGreater(f["size"], 0)
                self.assertEqual(o["size_mb"], round(sum(f["size"] for f in o.get("files", [])) / 1e6), o["id"])

    def test_every_setting_an_option_writes_is_editable(self):
        for g in models.groups():
            for o in g["options"]:
                for rule in [r for r in g["recommend"]] + [{"config": o.get("config", {})}]:
                    for key, value in rule.get("config", {}).items():
                        self.assertEqual(config_store.validate_update({key: value}), [], f"{o['id']}: {key}={value}")

    def test_speech_recognition_follows_the_hardware_table(self):
        def pick(**facts):
            base = {"nvidia_gpu": False, "nvidia_free_vram_gb": 0.0, "cuda_libs": False, "cpu_logical_cores": 4}
            base.update(facts)
            rec = models.recommend(base)["stt"]
            return rec["option"], rec["settings"]["stt.device"]
        self.assertEqual(pick(nvidia_gpu=True, nvidia_free_vram_gb=3.5, cuda_libs=True), ("whisper-medium", "cuda"))
        self.assertEqual(pick(nvidia_gpu=True, nvidia_free_vram_gb=2.0, cuda_libs=True), ("whisper-small", "cuda"))
        self.assertEqual(pick(nvidia_gpu=True, nvidia_free_vram_gb=8.0, cpu_logical_cores=12), ("whisper-small", "cpu"),
                         "a GPU without the CUDA libraries runs on the CPU")
        self.assertEqual(pick(cpu_logical_cores=8), ("whisper-small", "cpu"))
        self.assertEqual(pick(cpu_logical_cores=4), ("whisper-base", "cpu"))

    def test_settings_for_a_manual_choice_keep_the_hardware_part(self):
        facts = {"nvidia_gpu": False, "cpu_logical_cores": 16}
        self.assertEqual(models.settings_for("stt", "whisper-medium", facts),
                         {"stt.device": "cpu", "stt.compute_type": "int8", "stt.model_path": "medium"})
        with self.assertRaises(KeyError):
            models.settings_for("tts", "whisper-medium", facts)

    def test_downloaded_whisper_folders_are_what_the_transcriber_loads(self):
        _, option = models.find("whisper-small")
        folder = paths.models_dir() / option["dir"]
        self.assertEqual(Path(paths.whisper_model("small")[0]), folder) if folder.exists() else \
            self.assertEqual(folder, paths.models_dir() / "whisper" / "small")


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.data = {"/a.bin": bytes(range(256)) * 400, "/b.txt": b"vocabulary\n" * 50}
        self.server = FileServer(self.data)
        self.addCleanup(self.server.close)
        self.option = {"id": "test-model", "name": "Test model", "dir": f"test-{time.monotonic_ns()}",
                       "files": [entry("a.bin", self.data["/a.bin"], self.server.url + "/a.bin"),
                                 entry("b.txt", self.data["/b.txt"], self.server.url + "/b.txt")]}
        cat = {"groups": [{"id": "x", "title": "X", "explain": "x", "recommend": [], "options": [self.option]}]}
        patcher = mock.patch.object(models, "catalogue", lambda: cat)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events = []
        self.dl = downloads.Downloader(self.events.append, retry_delay=0.01)

    def wait(self, states=("done", "failed", "cancelled"), timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            job = next((j for j in self.dl.jobs() if j["option"] == "test-model"), None)
            if job and job["state"] in states:
                return job
            time.sleep(0.02)
        self.fail(f"download did not finish: {self.dl.jobs()}")

    def test_download_verify_and_status(self):
        self.assertEqual(models.status(self.option)["state"], "missing")
        self.assertEqual(self.dl.start("test-model"), "queued")
        job = self.wait()
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(models.status(self.option), {"state": "installed", "have": job["total"], "total": job["total"]})
        dest = models.target(self.option, self.option["files"][0])
        self.assertEqual(dest.read_bytes(), self.data["/a.bin"])
        self.assertFalse(dest.with_name("a.bin.part").exists())
        self.assertEqual(self.dl.start("test-model"), "installed", "nothing to do twice")

    def test_an_add_on_is_installed_after_the_files(self):
        from src.core import addons
        self.option["addon"] = "piper"
        state = {"installed": False, "calls": 0}

        def install(name):
            state["calls"] += 1
            state["installed"] = True
        with mock.patch.object(addons, "installed", lambda name: state["installed"]), \
                mock.patch.object(addons, "spec", lambda name: {"name": "the Piper voice engine"}), \
                mock.patch.object(addons, "install", install):
            self.assertEqual(self.dl.start("test-model"), "queued")
            job = self.wait()
            self.assertEqual(job["state"], "done", job)
            self.assertEqual(state["calls"], 1)
            self.assertIn("installing", [e["state"] for e in self.events])
            self.assertEqual(models.status(self.option)["state"], "installed")
            state["installed"] = False                   # files there, engine gone: not ready
            self.assertEqual(models.status(self.option)["state"], "partial")

    def test_a_failed_add_on_install_fails_the_download_in_plain_words(self):
        from src.core import addons
        self.option["addon"] = "piper"

        def install(name):
            raise RuntimeError("Installing the Piper voice engine failed: no internet")
        with mock.patch.object(addons, "installed", lambda name: False), \
                mock.patch.object(addons, "spec", lambda name: {"name": "the Piper voice engine"}), \
                mock.patch.object(addons, "install", install):
            self.dl.start("test-model")
            job = self.wait()
        self.assertEqual(job["state"], "failed")
        self.assertIn("no internet", job["error"])

    def test_a_dropped_connection_resumes_with_a_range_request(self):
        self.server.drop_first_after["/a.bin"] = 30000
        self.dl.start("test-model")
        self.assertEqual(self.wait()["state"], "done")
        ranges = [r for p, r in self.server.requests if p == "/a.bin"]
        self.assertEqual(ranges, [None, "bytes=30000-"])
        self.assertEqual(models.target(self.option, self.option["files"][0]).read_bytes(), self.data["/a.bin"])

    def test_a_checksum_mismatch_deletes_the_file_and_fails(self):
        self.option["files"][1]["sha256"] = "0" * 64
        self.dl.start("test-model")
        job = self.wait()
        self.assertEqual(job["state"], "failed")
        self.assertIn("checksum", job["error"])
        dest = models.target(self.option, self.option["files"][1])
        self.assertFalse(dest.exists() or dest.with_name("b.txt.part").exists())

    def test_a_missing_file_on_the_server_fails_in_plain_words(self):
        self.option["files"][0]["url"] = self.server.url + "/gone.bin"
        self.dl.start("test-model")
        job = self.wait()
        self.assertEqual(job["state"], "failed")
        self.assertIn("no longer offers", job["error"])

    def test_import_a_file_the_user_already_has(self):
        src = Path(tempfile.mkdtemp()) / "downloaded-by-hand.bin"
        src.write_bytes(self.data["/a.bin"])
        self.assertTrue(self.dl.import_file("test-model", "a.bin", str(src))["ok"])
        self.assertEqual(models.status(self.option)["state"], "partial")
        wrong = Path(tempfile.mkdtemp()) / "b.txt"
        wrong.write_bytes(b"something else")
        self.assertFalse(self.dl.import_file("test-model", "b.txt", str(wrong))["ok"])
        self.dl.start("test-model")
        self.assertEqual(self.wait()["state"], "done")
        self.assertEqual([p for p, _ in self.server.requests], ["/b.txt"], "the imported file was not downloaded again")

    def test_delete_frees_the_files(self):
        self.dl.start("test-model")
        self.assertEqual(self.wait()["state"], "done")
        freed = models.delete(self.option)
        self.assertEqual(freed, sum(f["size"] for f in self.option["files"]))
        self.assertEqual(models.status(self.option)["state"], "missing")
        self.assertFalse((paths.models_dir() / self.option["dir"]).exists(), "the empty folder goes too")

    def test_progress_events(self):
        self.dl.start("test-model")
        self.wait()
        states = [e["state"] for e in self.events]
        self.assertEqual(states[0], "queued")
        self.assertIn("verifying", states)
        self.assertEqual(states[-1], "done")
        self.assertEqual(self.events[-1]["done"], self.events[-1]["total"])


if __name__ == "__main__":
    unittest.main()
