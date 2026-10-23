"""The provider adapters against a fake HTTP server (no network): what they send and how they read replies.

Both adapters: plain text, one tool call, parallel tool calls, a failed tool result, an image result,
cancel mid-request, timeout, 429 with retry-after, a rejected key, the model list. OpenAI-compatible:
malformed tool JSON, schema sanitizing, vision guessing. Anthropic: prompt-cache breakpoints, signed
thinking blocks sent back unchanged, effort retried without, refusal fallback blocks.
"""
import threading
import time
import unittest

from src.agent.providers import ProviderError, api_key, credential_target, role, settings_for
from src.agent.providers.anthropic_api import AnthropicAdapter, answer_blocks, replayable
from src.agent.providers.base import Message, ToolCall, ToolDef, ToolResultItem
from src.agent.providers.openai_compat import OpenAICompatAdapter, guess_vision, sanitize_schema
from tests import fake_llm as F

TOOLS = [ToolDef("get_datetime", "Read the clock", {"type": "object", "properties": {}, "additionalProperties": False}),
         ToolDef("open_app", "Open an app", {"type": "object", "properties": {
             "app_name": {"type": "string", "enum": ["notepad"], "minLength": 1}}, "required": ["app_name"],
             "additionalProperties": False})]
PNG = {"data": "iVBORw0KGgo=", "mimeType": "image/png"}


def ask(adapter, messages=None, cancel=None, timeout=10.0, total=None):
    return adapter.complete("You are a test.", messages or [Message("user", text="hi")], TOOLS,
                            cancel or threading.Event(), timeout, total)


def tool_round(call_ids=("c1",), names=("get_datetime",), texts=("Friday 10:00",), images=None, error=False):
    calls = [ToolCall(id=i, name=n, args={}) for i, n in zip(call_ids, names)]
    results = [ToolResultItem(i, n, t, is_error=error, images=list(images or [])) for i, n, t in zip(call_ids, names, texts)]
    return [Message("user", text="what time is it"), Message("assistant", tool_calls=calls),
            Message("tool_results", results=results)]


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.server = F.FakeLLMServer()
        self.addCleanup(self.server.close)


# ---------------------------------------------------------------------- OpenAI-compatible

class OpenAICompatTests(ServerCase):
    def adapter(self, **settings):
        s = {"base_url": self.server.url + "/v1", "timeout_seconds": 10, "max_retries": 2}
        s.update(settings)
        a = OpenAICompatAdapter("custom", "model-a", s, api_key="sk-test")
        self.addCleanup(a.close)
        return a

    def test_plain_text_and_usage(self):
        self.server.push(F.openai_text("Hello there", prompt_tokens=120, cached=100, completion_tokens=4))
        comp = ask(self.adapter())
        self.assertEqual((comp.text, comp.stop_reason, comp.tool_calls), ("Hello there", "end", []))
        self.assertEqual((comp.usage.input_tokens, comp.usage.cache_read, comp.usage.output_tokens), (20, 100, 4))
        body = self.server.bodies("/chat/completions")[0]
        self.assertEqual(body["messages"][0], {"role": "system", "content": "You are a test."})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "hi"})
        self.assertEqual([t["function"]["name"] for t in body["tools"]], ["get_datetime", "open_app"])
        self.assertEqual(self.server.requests[0]["headers"]["authorization"], "Bearer sk-test")

    def test_single_tool_call(self):
        self.server.push(F.openai_tools(("call_1", "open_app", {"app_name": "notepad"})))
        comp = ask(self.adapter())
        self.assertEqual(comp.stop_reason, "tool_use")
        self.assertEqual([(c.id, c.name, c.args, c.error) for c in comp.tool_calls],
                         [("call_1", "open_app", {"app_name": "notepad"}, "")])

    def test_parallel_tool_calls_and_their_results_go_back_in_order(self):
        self.server.push(F.openai_tools(("a", "get_datetime", {}), ("b", "open_app", {"app_name": "notepad"})),
                         F.openai_text("Done"))
        a = self.adapter()
        comp = ask(a)
        self.assertEqual([c.id for c in comp.tool_calls], ["a", "b"])
        history = [Message("user", text="hi"),
                   Message("assistant", tool_calls=comp.tool_calls, raw=comp.raw, raw_from=a.kind),
                   Message("tool_results", results=[ToolResultItem("a", "get_datetime", "10:00"),
                                                    ToolResultItem("b", "open_app", "ERROR: no", is_error=True)])]
        self.assertEqual(ask(a, history).text, "Done")
        sent = self.server.bodies("/chat/completions")[1]["messages"]
        self.assertEqual([m["role"] for m in sent], ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual([c["id"] for c in sent[2]["tool_calls"]], ["a", "b"])
        self.assertEqual([(m["tool_call_id"], m["content"]) for m in sent[3:]], [("a", "10:00"), ("b", "ERROR: no")])

    def test_malformed_tool_arguments_are_flagged_and_sent_back_verbatim(self):
        self.server.push(F.openai_tools(("x", "open_app", '{"app_name": "note')), F.openai_text("ok"))
        a = self.adapter()
        comp = ask(a)
        self.assertIn("not valid JSON", comp.tool_calls[0].error)
        self.assertEqual(comp.tool_calls[0].args, {})
        history = [Message("user", text="hi"),
                   Message("assistant", tool_calls=comp.tool_calls, raw=comp.raw, raw_from=a.kind),
                   Message("tool_results", results=[ToolResultItem("x", "open_app", "INVALID ARGUMENTS", is_error=True)])]
        ask(a, history)
        sent = self.server.bodies("/chat/completions")[1]["messages"][2]
        self.assertEqual(sent["tool_calls"][0]["function"]["arguments"], '{"app_name": "note')

    def test_image_results_follow_the_tool_messages(self):
        self.server.push(F.openai_text("I see a window"))
        ask(self.adapter(vision=True), tool_round(names=("desktop_screenshot",), texts=("screenshot taken",),
                                                  images=[PNG]))
        sent = self.server.bodies("/chat/completions")[0]["messages"]
        self.assertEqual([m["role"] for m in sent], ["system", "user", "assistant", "tool", "user"])
        parts = sent[-1]["content"]
        self.assertIn("desktop_screenshot", parts[0]["text"])
        self.assertEqual(parts[1]["image_url"]["url"], "data:image/png;base64," + PNG["data"])

    def test_a_server_that_rejects_images_is_reported_as_such(self):
        self.server.push(F.openai_error(400, "This model does not support image input"))
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter(), tool_round(images=[PNG]))
        self.assertEqual(cm.exception.kind, "no_vision")

    def test_cancel_mid_request_returns_at_once(self):
        self.server.push(dict(F.openai_text("late"), delay=5))
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        t0 = time.time()
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter(), cancel=cancel)
        self.assertEqual(cm.exception.kind, "cancelled")
        self.assertLess(time.time() - t0, 2.0)

    def test_timeout(self):
        self.server.push(dict(F.openai_text("late"), delay=3))
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter(max_retries=0), timeout=0.5)
        self.assertEqual(cm.exception.kind, "timeout")

    def test_429_with_retry_after_is_retried(self):
        self.server.push(F.openai_error(429, "slow down", {"retry-after-ms": "20", "retry-after": "0"}),
                         F.openai_text("after the wait"))
        self.assertEqual(ask(self.adapter()).text, "after the wait")
        self.assertEqual(len(self.server.bodies("/chat/completions")), 2)

    def test_rate_limit_that_persists_is_reported(self):
        self.server.push(*[F.openai_error(429, "slow down", {"retry-after-ms": "10"})] * 3)
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter())
        self.assertEqual(cm.exception.kind, "rate_limit")
        self.assertIn("rate limiting", cm.exception.spoken())

    def test_rejected_key(self):
        self.server.push(F.openai_error(401, "Incorrect API key provided"))
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter())
        self.assertEqual(cm.exception.kind, "auth")
        self.assertEqual(len(self.server.bodies()), 1, "a rejected key is not retried")

    def test_unreachable_server(self):
        a = OpenAICompatAdapter("ollama", "m", {"base_url": "http://127.0.0.1:9/v1", "max_retries": 0}, None)
        with self.assertRaises(ProviderError) as cm:
            ask(a, timeout=3)
        self.assertEqual(cm.exception.kind, "network")

    def test_model_list(self):
        self.server.models = ["llama3.2", "qwen3:8b"]
        self.assertEqual(self.adapter().list_models(), ["llama3.2", "qwen3:8b"])

    def test_basic_schema_drops_validation_keywords_but_keeps_property_names(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["pattern"], "properties": {
            "pattern": {"type": "string", "minLength": 1, "maxLength": 9},
            "items": {"type": "array", "items": {"type": "integer", "minimum": 0}, "maxItems": 3}}}
        basic = sanitize_schema(schema, "basic")
        self.assertEqual(basic, {"type": "object", "required": ["pattern"], "properties": {
            "pattern": {"type": "string"}, "items": {"type": "array", "items": {"type": "integer"}}}})
        self.assertEqual(sanitize_schema(schema, "full"), schema)
        self.assertEqual(schema["properties"]["pattern"]["minLength"], 1, "the original is not modified")

    def test_vision_guess(self):
        self.assertTrue(guess_vision("llama3.2-vision"))
        self.assertTrue(guess_vision("qwen2.5vl:7b"))
        self.assertFalse(guess_vision("qwen3:8b"))
        self.assertTrue(OpenAICompatAdapter("x", "llava", {"base_url": "u"}).capabilities.vision)
        self.assertFalse(OpenAICompatAdapter("x", "llava", {"base_url": "u", "vision": False}).capabilities.vision)


# ---------------------------------------------------------------------- Anthropic

class AnthropicTests(ServerCase):
    def adapter(self, model="claude-test", **settings):
        s = {"base_url": self.server.url, "timeout_seconds": 10, "max_retries": 2, "max_tokens": 1000,
             "effort": "low"}
        s.update(settings)
        a = AnthropicAdapter("anthropic", model, s, api_key="sk-ant-test")  # export-scan: ignore
        self.addCleanup(a.close)
        return a

    def test_plain_text_usage_and_cache_breakpoints(self):
        self.server.push(F.anthropic_text("Hello", input_tokens=12, cache_read=900, cache_write=30, output_tokens=3))
        comp = ask(self.adapter())
        self.assertEqual((comp.text, comp.stop_reason), ("Hello", "end"))
        self.assertEqual((comp.usage.input_tokens, comp.usage.cache_read, comp.usage.cache_write, comp.usage.output_tokens),
                         (12, 900, 30, 3))
        self.assertEqual(comp.usage.prompt_tokens, 942)
        body = self.server.bodies("/v1/messages")[0]
        self.assertEqual(body["system"], [{"type": "text", "text": "You are a test.", "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(body["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertTrue(body["stream"])
        self.assertEqual(body["tools"][1]["input_schema"]["properties"]["app_name"]["enum"], ["notepad"])
        headers = self.server.requests[0]["headers"]
        self.assertIn("sk-ant-test", headers.get("x-api-key", "") + headers.get("authorization", ""))  # export-scan: ignore

    def test_tool_calls_parallel_and_results_in_one_user_message(self):
        self.server.push(F.anthropic_tools(("t1", "get_datetime", {}), ("t2", "open_app", {"app_name": "notepad"}),
                                           text="Let me check."),
                         F.anthropic_text("It is ten."))
        a = self.adapter()
        comp = ask(a)
        self.assertEqual(comp.stop_reason, "tool_use")
        self.assertEqual(comp.text, "Let me check.")
        self.assertEqual([(c.id, c.name, c.args) for c in comp.tool_calls],
                         [("t1", "get_datetime", {}), ("t2", "open_app", {"app_name": "notepad"})])
        history = [Message("user", text="hi"),
                   Message("assistant", text=comp.text, tool_calls=comp.tool_calls, raw=comp.raw, raw_from=a.kind),
                   Message("tool_results", results=[ToolResultItem("t1", "get_datetime", "10:00"),
                                                    ToolResultItem("t2", "open_app", "ERROR: failed", is_error=True,
                                                                   images=[PNG])])]
        self.assertEqual(ask(a, history).text, "It is ten.")
        sent = self.server.bodies("/v1/messages")[1]["messages"]
        self.assertEqual([m["role"] for m in sent], ["user", "assistant", "user"])
        self.assertEqual([b["type"] for b in sent[1]["content"]], ["text", "tool_use", "tool_use"])
        results = sent[2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["t1", "t2"])
        self.assertNotIn("is_error", results[0])
        self.assertTrue(results[1]["is_error"])
        self.assertEqual(results[1]["content"][1], {"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": PNG["data"]}})

    def test_signed_thinking_goes_back_unchanged(self):
        self.server.push(F.anthropic_stream([{"type": "thinking", "thinking": "", "signature": "sig-123"},
                                             {"type": "tool_use", "id": "t1", "name": "get_datetime", "input": {}}],
                                            stop_reason="tool_use"),
                         F.anthropic_text("done"))
        a = self.adapter()
        comp = ask(a)
        history = [Message("user", text="hi"),
                   Message("assistant", tool_calls=comp.tool_calls, raw=comp.raw, raw_from=a.kind),
                   Message("tool_results", results=[ToolResultItem("t1", "get_datetime", "10:00")])]
        ask(a, history)
        block = self.server.bodies("/v1/messages")[1]["messages"][1]["content"][0]
        self.assertEqual((block["type"], block["signature"]), ("thinking", "sig-123"))

    def test_a_model_without_effort_is_retried_without_it(self):
        self.server.push(F.anthropic_error(400, "invalid_request_error", "output_config.effort: not supported by this model"),
                         F.anthropic_text("fine"))
        a = self.adapter()
        self.assertEqual(ask(a).text, "fine")
        first, second = self.server.bodies("/v1/messages")
        self.assertIn("output_config", first)
        self.assertNotIn("output_config", second)
        ask_again = F.anthropic_text("again")
        self.server.push(ask_again)
        ask(a)
        self.assertNotIn("output_config", self.server.bodies("/v1/messages")[2], "remembered for the session")

    def test_cancel_mid_request(self):
        self.server.push(dict(F.anthropic_text("late"), delay=5))
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        t0 = time.time()
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter(), cancel=cancel)
        self.assertEqual(cm.exception.kind, "cancelled")
        self.assertLess(time.time() - t0, 2.0)

    def test_timeout(self):
        self.server.push(dict(F.anthropic_text("late"), delay=3))
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter(max_retries=0), timeout=0.5)
        self.assertEqual(cm.exception.kind, "timeout")

    def test_429_with_retry_after_is_retried(self):
        self.server.push(F.anthropic_error(429, "rate_limit_error", "slow down", {"retry-after-ms": "20", "retry-after": "0"}),
                         F.anthropic_text("ok now"))
        self.assertEqual(ask(self.adapter()).text, "ok now")
        self.assertEqual(len(self.server.bodies("/v1/messages")), 2)

    def test_overloaded_and_rejected_key(self):
        self.server.push(*[F.anthropic_error(529, "overloaded_error", "Overloaded", {"retry-after-ms": "10"})] * 3)
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter())
        self.assertEqual(cm.exception.kind, "overloaded")
        self.server.push(F.anthropic_error(401, "authentication_error", "invalid x-api-key"))
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter())
        self.assertEqual(cm.exception.kind, "auth")

    def test_prompt_too_long(self):
        self.server.push(F.anthropic_error(400, "invalid_request_error", "prompt is too long: 1200000 tokens > 1000000"))
        with self.assertRaises(ProviderError) as cm:
            ask(self.adapter())
        self.assertEqual(cm.exception.kind, "context_length")

    def test_model_list(self):
        self.server.models = ["claude-haiku-5-5", "claude-sonnet-5-5"]
        self.assertEqual(self.adapter().list_models(), ["claude-haiku-5-5", "claude-sonnet-5-5"])

    def test_refusal_fallback_only_for_supported_models_on_the_official_api(self):
        official = AnthropicAdapter("anthropic", "claude-opus-5-5", {"base_url": "https://api.anthropic.com"}, "k")
        req = official.request("s", [Message("user", text="hi")], [])
        self.assertEqual((req["fallbacks"], req["betas"]), ("default", ["server-side-fallback-2026-07-01"]))
        self.assertNotIn("fallbacks", AnthropicAdapter("anthropic", "claude-haiku-5-5", {}, "k").request("s", [], []))
        proxy = AnthropicAdapter("anthropic", "claude-opus-5-5", {"base_url": "http://127.0.0.1:1"}, "k")
        self.assertNotIn("fallbacks", proxy.request("s", [], []))

    def test_blocks_before_a_fallback_are_not_replayed_or_run(self):
        content = [{"type": "thinking", "signature": "a"}, {"type": "text", "text": "partial "},
                   {"type": "tool_use", "id": "x", "name": "open_app", "input": {}},
                   {"type": "fallback", "from": {"model": "m1"}, "to": {"model": "m2"}},
                   {"type": "text", "text": "answer"}, {"type": "tool_use", "id": "y", "name": "get_datetime", "input": {}}]
        self.assertEqual([b["type"] for b in replayable(content)], ["text", "fallback", "text", "tool_use"])
        self.assertEqual([b["type"] for b in answer_blocks(content)], ["text", "tool_use"])
        self.assertEqual(replayable(content[:3]), content[:3], "without a fallback everything goes back")


# ---------------------------------------------------------------------- settings and keys

class ProviderSettingsTests(unittest.TestCase):
    CFG = {"llm": {"main": {"provider": "ollama"}, "utility": {"provider": "", "model": ""},
                   "timeout_seconds": 120, "history_budget_tokens": 60000,
                   "providers": {"ollama": {"kind": "openai_compat", "local": True, "model": "qwen3:8b",
                                            "base_url": "http://127.0.0.1:11434/v1",
                                            "utility_model": "", "history_budget_tokens": 12000},
                                 "anthropic": {"kind": "anthropic", "needs_key": True, "env_key": "JARVIS_TEST_KEY",
                                               "model": "claude-sonnet-5-5", "utility_model": "claude-haiku-5-5"}}}}

    def test_roles(self):
        provider, model, s = role(self.CFG, "main")
        self.assertEqual((provider, model, s["history_budget_tokens"], s["timeout_seconds"]), ("ollama", "qwen3:8b", 12000, 120))
        self.assertEqual(role(self.CFG, "utility")[:2], ("ollama", "qwen3:8b"), "no utility model: the main model")
        cfg = {"llm": dict(self.CFG["llm"], utility={"provider": "anthropic", "model": ""})}
        self.assertEqual(role(cfg, "utility")[:2], ("anthropic", "claude-haiku-5-5"))
        with self.assertRaises(ProviderError):
            settings_for(self.CFG, "nope")

    def test_key_from_the_secret_store_then_the_environment(self):
        from unittest import mock
        from src.core import credentials
        s = settings_for(self.CFG, "anthropic")
        with mock.patch.object(credentials, "read", lambda target: None), \
                mock.patch.dict("os.environ", {"JARVIS_TEST_KEY": "from-env"}):
            self.assertEqual(api_key("anthropic", s), ("from-env", "env:JARVIS_TEST_KEY"))
        with mock.patch.object(credentials, "read", lambda target: "stored" if target == credential_target("anthropic") else None):
            self.assertEqual(api_key("anthropic", s), ("stored", "saved"))

    def test_problems_name_what_is_missing(self):
        from unittest import mock
        from src.agent.providers import problems
        from src.core import credentials
        cfg = {"llm": dict(self.CFG["llm"], main={"provider": "anthropic"})}
        with mock.patch.object(credentials, "read", lambda target: None), mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("JARVIS_TEST_KEY", None)
            self.assertTrue(any("no API key" in p for p in problems(cfg)))
        self.assertEqual(problems(self.CFG), [], "a local server needs no key")


if __name__ == "__main__":
    unittest.main()
