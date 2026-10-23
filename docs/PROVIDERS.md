# AI providers

Jarvis needs one AI model to understand requests and decide which tools to run. You choose it in
**Settings › AI**. Everything else (your tools, permissions, approvals, memory) works the same whichever
provider you pick.

| Provider | What you need | Cost | Notes |
|---|---|---|---|
| **Claude (your Claude account)** — default | A Claude plan, and Claude Code installed and signed in | Included in your plan (its usage limits apply) | Runs through Claude Code, Anthropic's official app. Jarvis never sees your Claude login. |
| Anthropic API | An API key | Per token | Prompt caching is on, so later requests in a conversation cost much less. |
| OpenAI | An API key | Per token | |
| OpenRouter | An API key | Per token | One key for many model makers. |
| Groq | An API key | Per token | Very fast. |
| Google Gemini | An API key | Per token | Uses Gemini's OpenAI-compatible endpoint. |
| Ollama (on this PC) | Ollama running, a model pulled | Free | Nothing leaves your PC. Needs a capable GPU for good results. |
| LM Studio (on this PC) | LM Studio's server running, a model loaded | Free | Nothing leaves your PC. |
| Another OpenAI-compatible server | Its address (base URL), maybe a key | Depends | vLLM, llama.cpp server, a company gateway, ... |

## Setting one up

1. Open **Settings › AI** and choose the **Provider**.
2. For an API provider, paste your key and click **Save key**. Keys go to Windows Credential Manager
   (`JARVIS/LLM/<provider>`), never into a settings file, a log or a prompt.
3. Click **Fetch models** and pick a **Model** (or type its id).
4. Click **Save**, then **Test connection**: Jarvis asks the model what time it is, and the model has to use
   the clock tool to answer. A pass means tools work end to end.
5. Restart the assistant when Settings asks you to.

Developers can use environment variables instead of saved keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`OPENROUTER_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`. A key saved in Settings wins over the environment.

## Models on this PC (Ollama, LM Studio)

- Pick a model that supports **tool calling** (for example recent Qwen, Llama or Mistral models). A model
  without tool support can chat but can't do anything on the PC.
- Jarvis sends a few thousand tokens of instructions and tool descriptions with every request. Give the
  model a context of **at least 16k tokens**: in Ollama set `OLLAMA_CONTEXT_LENGTH=16384` (or more) before
  starting it, or create a model with `PARAMETER num_ctx 16384`; in LM Studio set the context length when
  loading the model. With a smaller context the model silently loses the start of its instructions.
- Local providers get the **short tool list** by default (Settings › AI › Tools the model sees): apps,
  media, the clock, memory, web reading, read-only project files and tasks. Small models make far fewer
  mistakes with it. Choose "All tools" if your model copes.
- The first request after starting can be slow while the model loads; Jarvis sends one small request at
  start-up so that happens before you speak.

## Two roles: main and background

- **Main**: the agent you talk to.
- **Background jobs**: greetings and the summary that replaces a long conversation. By default this is the
  main provider's small model (Claude Haiku for Claude and the Anthropic API; the main model otherwise).
  You can point it at another provider, for example a local model to keep background work free.

## Cost and the daily limit

Every request's tokens are recorded. **Activity › AI usage** shows requests, tokens and the estimated cost
per day. Estimates use the prices in `llm.prices` (USD per million tokens, per model id). Claude models are
listed; for other models add a line to your `config.json` in the data folder:

```json
{"llm": {"prices": {"my-model-id": {"input": 0.5, "output": 1.5}}}}
```

Set **Daily spending limit** in Settings › AI to stop new requests once today's estimated cost reaches it;
Jarvis says so out loud and works again the next day. Models on this PC and your Claude plan are not
counted.

## Hidden coding-agent runs

When you explicitly ask for a hidden run ("in the background, have the coding agent fix the tests in the
shop project"), Jarvis runs a coding agent CLI in that project folder and waits for it. Choose which one
under **Settings › Tools and permissions › Coding agent for hidden runs**:

| Setting | Command |
|---|---|
| Claude Code | `claude -p <task>` |
| Codex | `codex exec <task>` |
| Aider | `aider --yes-always --message <task>` |
| Custom | `coding_agent.custom_command` from `config.json`, with the task added at the end |
| Off | The tool is not offered |

Jarvis asks before every run (unless you turn that off) and saves the output in its cache folder, never in
the project. Working in a visible terminal ("open a terminal in the shop project and start Claude") works
with any of them and is usually the better choice.

## Checking a provider from the command line

```
python -m src.agent check                                   # the provider in your settings
python -m src.agent check --provider ollama --model qwen3:8b
python -m src.agent check --provider openai --model <model-id> "what time is it"
python -m src.agent check --provider custom --base-url http://127.0.0.1:8000/v1 --model my-model
```

Tools run in dry-run mode unless you add `--live`, approvals are automatically denied, and a temporary
memory database is used.

## How it works (for contributors)

- `src/agent/providers/`: one adapter per wire format. `anthropic_api.py` (Messages API, streamed, prompt
  caching, signed thinking blocks sent back unchanged) and `openai_compat.py` (chat completions with tools)
  cover every provider above. Both use the official SDKs and turn their errors into `ProviderError` kinds.
- `src/agent/tool_loop_backend.py`: the agent loop for API providers. Every tool call goes through the same
  `PolicyExecutor` as with Claude Code. The conversation only ever grows; past `history_budget_tokens` the
  next turn starts from a summary written by the background model.
- `src/agent/claude_code_backend.py`: the Claude (your account) option, through the Claude Agent SDK.
- `tests/test_backend_contract.py` runs the same conversations against every backend;
  `tests/test_providers.py` checks both adapters against a fake HTTP server (`tests/fake_llm.py`).
