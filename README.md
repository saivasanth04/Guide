# Routed generation with a Google-style client interface

`main.py` is a single generation call:

```python
from ntg_llm import LLMClient

client = LLMClient()
response = client.models.generate_content(
    model="router",
    contents="Explain generative AI in one sentence.",
)
print(response.text)
```

Run `python main.py`. Edit `contents` to build your application directly; there is
no command-line prompt, agent, tool loop or project executor.

The public method has the same keyword argument names as Google's
`client.models.generate_content(*, model, contents, config=None)`.
The implementation uses our existing provider routing, credential pool, persistent
rotation, failover and sanitized errors; it does not create a separate Google client.
Credentials stay in `.env`, with provider/model settings in `llm.json` and `models.json`.

Routing latency settings in `.env`:

```dotenv
RETRIES_PER_DEPLOYMENT=0
PROVIDER_REQUEST_TIMEOUT=30
COMPLETION_TIME_BUDGET=60
COMPLETION_MAX_DEPLOYMENTS=all
```

Each LLM routing request has one monotonic deadline. Each provider attempt receives
the smaller of its timeout and the remaining routing budget. Failed, timed-out,
overloaded, invalid-response, and rate-limited deployments immediately fail over
without retry sleeps. The legacy retry setting is accepted for configuration
compatibility; it no longer enables repeated attempts against a failed deployment.
429 responses enter deployment cooldown using `Retry-After`, or 30 seconds if the
header is absent or invalid. Access-enforcement stops remain terminal.

A bounded caller wait protects against synchronous SDKs that ignore timeouts.
Such a call may finish in a daemon thread after the caller moves on; its late
result cannot update the routing ledger. The deadline applies per LLM request,
not to the entire multi-turn agent run, filesystem calls, or initial imports.
Budget exhaustion raises a sanitized 504 with attempt diagnostics. Round-robin
selection and specific-model scoping remain unchanged.

Install coding-agent dependencies with `python -m pip install -e ".[dev,agent]"`.
For sandbox execution, set `E2B_API_KEY` in `.env`; `E2B_TEMPLATE` is optional.
The agent shares its workspace with the E2B executor. Source files sync before
each command, and the sandbox persists until agent cleanup. Remote-only artifacts
and dependencies remain between calls; sandbox edits are not downloaded locally.
The tool description provides its Linux working directory and synchronization limits.
Run the optional live MCP/E2B check in PowerShell with
`$env:NTG_LIVE_E2B='1'; python -m pytest -q tests/test_e2b_sandbox.py`.
The tests include real local HTTP transport and an end-to-end ReAct run using the
MCP filesystem server (requires Node.js/npx). These require no cloud credentials.

- `model="router"` rotates across eligible configured deployments.
- A specific model, such as `model="gemini-3.8-flash"`, rotates only among matching
  configured deployments/keys. It never silently switches to another model.
- `contents` accepts text, lists of text/text parts, and user/model Content dictionaries.
  Native SDK text Content/Part objects are accepted when that SDK is installed.
- `config` accepts a dictionary or native SDK GenerateContentConfig object.
- `response.text` is the generated text; `response.model_version` identifies the
  model actually used. Routing evidence remains in `response.diagnostics`.

```python
response = client.models.generate_content(
    model="router",
    contents="Explain generative AI in one sentence.",
    config={
        "system_instruction": "Answer clearly and briefly.",
        "temperature": 0.2,
        "max_output_tokens": 128,
    },
)
print(response.text)
```

Supported generation settings: system_instruction, temperature, top_p, top_k,
max_output_tokens, seed, stop_sequences, presence_penalty, frequency_penalty,
and candidate_count=1. Settings are validated and forwarded to the selected
provider; availability can still depend on that provider/model.

For structured output, use response_mime_type="application/json" and optionally
response_schema=YourPydanticModel. `response.parsed` then contains the validated
model; without a schema it contains the decoded JSON value. Default text generation
returns plain text directly and does not require an Answer JSON envelope.

This facade currently covers synchronous text generation. Multimodal parts, tools,
streaming, multiple candidates and other unsupported config fields are rejected;
it is not a complete replacement for every Google SDK feature. Raw schema dicts
and Google Schema objects are not supported as response_schema; use a named
Pydantic model. The existing `client.chat.completions.create(...)` and
`structured_completion(...)` APIs remain compatible.

Install with `python -m pip install -e ".[dev]"`. Test with
`python -B -m pytest -p no:cacheprovider -q` and
`python -B -m ruff check --no-cache .`.

Reference: [official Google Python SDK](https://github.com/googleapis/python-genai#generate-content).
