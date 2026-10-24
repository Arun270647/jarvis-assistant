"""scripts/check_links.py with a fake server: which links it collects, and how it judges downloads (size), pages and
the providers' API addresses. Nothing goes to the network."""
import io
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_links  # noqa: E402


class FakeResponse(io.BytesIO):
    def __init__(self, status=200, headers=None):
        super().__init__(b"x")
        self.status, self.headers = status, headers or {}


def answer(status=200, headers=None):
    def opener(request, timeout=None):
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, None)
        return FakeResponse(status, headers)
    return opener


class CheckLinksTests(unittest.TestCase):
    def test_collects_every_kind_of_link_jarvis_depends_on(self):
        links = check_links.collect()
        urls = {link["url"] for link in links}
        kinds = {link["kind"] for link in links}
        self.assertEqual(kinds, {"download", "page", "pypi", "api"})
        self.assertIn("https://pypi.org/pypi/kokoro-onnx/0.6.1/json", urls)
        self.assertIn("https://api.openai.com/v1/models", urls)
        self.assertFalse([u for u in urls if "localhost" in u or "127.0.0.1" in u], "local servers are skipped")
        self.assertTrue(all(link["size"] for link in links if link["kind"] == "download" and "models.json" in link["source"]))

    def test_a_download_must_have_the_catalogued_size(self):
        link = {"kind": "download", "url": "https://example.com/model.onnx", "size": 100}
        self.assertTrue(check_links.check(link, answer(206, {"Content-Range": "bytes 0-0/100"}))["ok"])
        bad = check_links.check(link, answer(206, {"Content-Range": "bytes 0-0/99"}))
        self.assertFalse(bad["ok"])
        self.assertIn("catalogue says 100", bad["detail"])
        self.assertTrue(check_links.check(link, answer(200, {"Content-Length": "100"}))["ok"], "a server without ranges")

    def test_pages_must_answer_and_apis_only_need_to_be_reachable(self):
        page = {"kind": "page", "url": "https://example.com/gone"}
        self.assertFalse(check_links.check(page, answer(404))["ok"])
        api = {"kind": "api", "url": "https://api.example.com/v1/models"}
        self.assertTrue(check_links.check(api, answer(401))["ok"], "no key: 401 means it is there")
        self.assertFalse(check_links.check(api, answer(503))["ok"])


if __name__ == "__main__":
    unittest.main()
