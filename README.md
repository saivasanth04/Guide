# Provider-neutral LLM abstraction — Milestone 9

Application code uses one client regardless of whether Gemini, Groq, OpenRouter,
or another configured LiteLLM provider supplies the answer.

Milestone 9 adds a separate Tester role and sandbox-backed repair workflow.
With `--verify`, the Coder writes project files, Docker executes the selected
checks, the Tester turns failures into feedback, and LangGraph schedules at most
three repairs by default. See [Milestone 9 usage and verification](docs/milestone9.md).
Run `python main.py` in a terminal to enter a task interactively, or supply the task
as an argument. The ready-to-run verified example is
`.\examples\milestone9\run.ps1` after preparing Docker and the Python image.

Milestone 6 uses a compiled LangGraph `StateGraph` to schedule model decisions,
tool execution, and observation-driven continuation. The graph ends on a validated
final answer. An `NTGChatModel` adapter sends every model call through the existing
NTG client and router. `langchain-core` supplies shared model/tool interfaces;
`langchain-classic` and its executor are no longer required.
See [Milestone 6 architecture and usage](docs/milestone6.md).
One Python Coder can read, write, and list files, then return validated structured
output. See [Milestone 5 tool policy](docs/milestone5.md).
The [tool-free Agent](docs/milestone4.md) and
[structured completion interface](docs/milestone3.md) remain available.

```powershell
python -m pip install -e ".[dev]"
python main.py "Write a Python function that squares a number" --context "Use no external packages."
python -m pytest -q
```

## Workspace tools

Enable filesystem actions in an existing directory:

```powershell
New-Item -ItemType Directory -Force .\agent_workspace
python main.py "Create a Python module that normalizes whitespace" --workspace .\agent_workspace
```

```python
from ntg_llm import LLMClient, create_coder_agent, create_filesystem_registry

tools = create_filesystem_registry("agent_workspace")
coder = create_coder_agent(LLMClient(), tools=tools, max_tool_calls=8)
result = coder.run("Inspect the workspace and implement the requested helper")
print(result.model_dump_json(indent=2))
```

The model selects a registered `read_file`, `write_file`, or `list_files` request.
The graph tool node dispatches a typed tool whose NTG handler validates and executes
it; the returned observation becomes graph state for the next model decision.
Files are scoped to the explicit workspace. Protected credential/state paths,
traversal, and filesystem links are rejected. Replacement requires host
`--allow-overwrite` and a tool request with `overwrite=true`.

`--max-tool-calls=N` defaults to 8 and maps to LangGraph's `recursion_limit=2*N`.
Each model/tool pair uses two graph steps; a final decision ends in the model node.
A run needing three tools plus a final answer requires a limit of at least four.
Exhaustion returns a sanitized 508. There is no separate host action counter or
extra final turn.
Filesystem payload/list bounds are configurable
host policy, separate from provider quotas. Successful actions appear in
`diagnostics.tool_calls`; model turns and upstream failures appear in
`agent_steps`, `agent_step_count`, `attempts`, and `failed_attempts`.
Diagnostics identify `agent_runtime` as `langgraph.StateGraph`.
Traces omit file contents, scratchpads, and model reasoning. Graph debug output and LangSmith tracing are disabled for agent execution.
If a later turn fails, earlier writes remain and are reported in error diagnostics.
Tool actions are never automatically retried or replayed by provider recovery.

See [the current lifecycle and diagnostics](docs/milestone6.md) and
[filesystem policy](docs/milestone5.md#workspace-policy).
Without `--workspace` or a supplied registry, the Agent retains its tool-free
behavior. Generated-code execution is available only through the explicit
Milestone 9 `--verify` Docker workflow; filesystem tools expose no shell.

## Agent interface

```python
from ntg_llm import LLMClient, create_coder_agent

coder = create_coder_agent(LLMClient())
result = coder.run("Implement a Python function that squares a number")
print(result.output.code)
print(result.model_dump_json(indent=2))
```

`Agent(name, system_prompt, llm, response_model=YourModel)` is the general-purpose
interface. `run(task, context=None)` returns an `AgentResult` with `agent_name`,
typed `output`, selected `model`, and routing `diagnostics`. Runs keep their task
and context isolated and delegate retries/fallback to the existing router.

`main.py` accepts a task argument and optional `--context` text. In a terminal,
omitting the task prompts for it. Noninteractive callers must supply a task.
`python main.py --help` displays usage. Add `--verify` for the Tester/repair flow.
The Coder returns nonblank `code` and `explanation` fields and checks Python source
with `ast.parse`. In tool-free mode, source is returned without execution or file creation;
parsing does not establish correctness. Invalid generated output returns a
sanitized error rather than triggering an agent repair loop.

## Direct LLM interface

```python
from ntg_llm import Answer, LLMClient


client = LLMClient()

response = client.chat.completions.create(
    model="router",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Say hello in one sentence."},
    ],
    response_model=Answer,
)

print(response.choices[0].message.parsed.model_dump_json(indent=2))
```

This direct completion interface remains available alongside the Agent interface.
Credentials and routing are separate from application code.
`response.model` and `response.diagnostics` expose the selected model and sanitized
provider/key-label/project information when needed. API keys are never included.
`diagnostics.failed_attempts` lists every failed attempt before the final answer,
including `account_label`, `key_label`, `provider`, `selected_model`, HTTP status,
sanitized error, attempt number, and retry number (0 means the initial attempt).
`attempts` includes those failures plus the successful attempt; `attempt_count`
is the total. A first-attempt success has an empty `failed_attempts` list.
Historically disabled deployments skipped before dispatch are not counted as
failed attempts for a new request, because they were not called in that request.
The adapter supports non-streaming text messages with system/user/assistant roles.

The original structured interface remains available:

```python
from llm import structured_completion, LLMError


try:
    result = structured_completion("What is 2 + 2?")

    print(result["answer"])

except LLMError as error:
    print(error.as_dict())
```

By default, responses are validated as a JSON object containing exactly one
nonempty string `answer`. `message.parsed` contains the validated Pydantic object.
For compatibility, default chat completions expose the answer text as
`message.content`; custom schemas expose serialized JSON there. Application code
should use `message.parsed` for structured data. The response itself is also a
Pydantic `ChatCompletion`: `response.model_dump_json()` serializes the complete
response, including the validated object and safe diagnostics.
`ChatClient` remains a compatibility name for `LLMClient`. Expected CLI errors
are displayed as sanitized JSON with provider retry timing, without tracebacks.

## Custom structured output

Define named fields using Pydantic models, including nested models and enums. The output shape belongs to the caller;
there is no built-in planning or task-generation behavior:

```python
from pydantic import BaseModel

from ntg_llm import LLMClient


class Program(BaseModel):
    language: str

    code: str


class ProgrammingAnswer(BaseModel):
    answer: str

    program: Program


response = LLMClient().chat.completions.create(
    messages=[{"role": "user", "content": "Write a Python Fibonacci program."}],
    response_model=ProgrammingAnswer,
)

result = response.choices[0].message.parsed

print(result.program.code)
```

The router sends the requested JSON schema to schema-capable deployments.
Deployments configured with `response_mode: "json_object"` receive the schema in
an additional system instruction and undergo the same local validation; JSON
object mode alone does not guarantee schema adherence upstream. Configure only
models that support the selected response mode. There is no plain-text recovery.

Validation rejects malformed JSON, duplicate keys, missing fields, unknown fields
(including nested fields), invalid enum values, and incorrect types. All declared
fields are required in the generated contract, including fields with defaults;
use a nullable field when null is allowed. Open-ended dictionaries are rejected
before dispatch: describe predictable object fields with nested Pydantic models.
Invalid output returns a sanitized 502 without retry or fallback. Invalid schema
or message input returns 400 before rotation advances. Raw failed output is never
included in diagnostics. Provider support for JSON Schema features varies; an
unsupported schema is surfaced as a provider error rather than weakened silently.

`structured_completion(prompt)` preserves its `{answer, diagnostics}` result.
`structured_completion(prompt, response_model=ProgrammingAnswer)` returns
`{data: <validated object>, diagnostics: ...}`. Routing diagnostics remain separate
from the caller's fields, preventing field-name collisions.

## Project layout

```text
main.py                   Coder CLI with optional explicit workspace tools
ntg_llm/
    __init__.py           Public application API
    agent.py              Generic stateless Agent and typed AgentResult
    coder.py              First Coder role and Python output validation
    project_workflow.py   Outer Coder -> sandbox -> Tester repair graph
    sandbox.py            Bounded Docker execution and snapshot provenance
    tester.py             Evidence-grounded Tester feedback
    testing_models.py     Typed policy, execution and project results
    testing_harness.py    Read-only trusted unittest harness
    tool_runtime.py       Compile/invoke the LangGraph ReAct graph
    langchain_model.py    NTGChatModel delegates model calls to LLMClient
    langchain_tools.py    Typed LangChain tools backed by the safe NTG registry
    agent_trace.py        Sanitized per-run routing and action evidence
    tools/                Typed registry, tool models, workspace filesystem policy
    client.py             OpenAI-style facade and lazy service construction
    schemas.py            Pydantic contracts and strict JSON validation
    responses.py          Typed, JSON-serializable chat response envelopes
    settings.py           Local secrets/configuration and service construction
    configuration.py      Provider account configuration parsing
    service.py            Completion orchestration and retry/fallback policy
    strategy.py           LiteLLM pinned-deployment adapter
    store.py              Transactional SQLite rotation and access state
    provider_errors.py    Sanitized provider error classification
    errors.py             Shared application error contract
    defaults.py           Default model names and legacy identity mapping
    runtime.py            Offline LiteLLM metadata/logging setup
    cli.py                Safe terminal error formatting
tests/                    Offline tests grouped by responsibility
pyproject.toml            Package metadata, dependencies, pytest configuration
```

Root `llm.py`, `chat_client.py`, `routing.py`, `configuration.py`, and
`provider_errors.py` remain compatibility imports. New code should import
`ntg_llm`. Run `python main.py "Your coding task"` from the repository. Configuration files,
`.env`, and the SQLite database remain in place; rotation identities are preserved.
Relative paths in `.env` resolve against its directory. The default `.env` is at
the repository root (alongside the package); an installed deployment can construct
its service with `ntg_llm.settings.load_service(absolute_env_path)` and pass it
to `LLMClient(service=service)`. This also provides a test injection seam.

Tests use temporary databases and synthetic provider responses; their shared
fixture blocks live HTTP. Run `python -m pytest -q`, `python -m ruff check .`, and
`python -m ruff format --check .`. Never add credentials to
source files, test fixtures, or diagnostics.

## Round-robin behavior

Order is account1/model1 -> account1/model2 -> account1/model3 ->
account2/model1 -> account2/model2 -> account2/model3 -> repeat.
SQLite transactions serialize selection and preserve rotation across processes
and restarts. Same-deployment retries do not rewind or advance that cursor.

**There are no local RPM, TPM, RPD, daily resets, shared quota budgets, token
reservations, prompt-size admission checks, or imposed output-token caps.**
The application does not count or predict provider quota usage. Historical usage
rows in an existing database are untouched and never consulted or incremented.
Former quota/token-budget fields in older configuration files are ignored.

The existing failure-handling behavior remains:

- Temporary provider HTTP 408/500/502/503/504: retry the same account/model twice
  by default, waiting 1 and 2 seconds plus up to 25 percent jitter.
- After those attempts fail, move to the next deployment in round-robin order.
- Confirmed model-specific 404: persist unavailability and advance without retry.
- Actual provider 429: surface the error. If the provider supplies Retry-After,
  honor it on future selection. No cooldown is invented when the header is absent.
- Ordinary permission/authentication errors (401/403): skip only the failed
  deployment, persist its access denial, and advance without retrying it.
- Explicit account suspension or reported leaked-key enforcement: retain a global
  stop. Those enforcement errors do not trigger fallback.
- Invalid JSON or malformed application input: surface a validation error.

Actual provider limits still apply. The application does not add assumed limits.
A selection-side 429 can occur only when every eligible deployment is waiting
for a real provider Retry-After. All-disabled models return a descriptive 503.

`RETRIES_PER_DEPLOYMENT=2` controls same-deployment retries.
`COMPLETION_MAX_DEPLOYMENTS=all` lets fallback traverse every configured deployment
without wrapping around within a request. Set a positive integer instead to cap
the distinct deployments considered. Recovery stops after success or exhausting
the eligible sequence. Permission failures never consume same-model retries. Each attempt has
a 60-second network timeout. LiteLLM/SDK internal retries and fallback lists stay
disabled so application recovery is explicit. Recovery diagnostics list attempts.

## Configure providers

`LLM_CONFIG=llm.json` in `.env` selects the provider-neutral configuration.
The supplied file keeps the original Gemini setup through
`include_legacy_gemini: true`. It does not enable other providers without keys.

Use `llm.example.json` as a reference:

1. Copy an account into `llm.json` and set `enabled` to true.
2. Put its credential in `.env`; `api_key_env` references that variable by name.
3. Set its provider and provider-relative model IDs, in desired order.
4. Choose `json_schema` or `json_object` as `response_mode`, according to model
   support. Both modes receive identical local answer validation.

Explicit accounts rotate in configured order, with each account's models in
configured order. Imported Gemini accounts follow them. Set
`include_legacy_gemini: false` to use only explicit accounts. Other API-key-based
LiteLLM providers can use this configuration when the selected model supports
the chosen JSON mode. Custom OpenAI-compatible endpoints use provider `openai`
and optional `api_base_env`, referencing an endpoint URL in `.env`.

`account_id` is an optional stable routing identity, defaulting to the project
ID or label. It is not a quota setting. The old `quota_scope` name is accepted
as a compatibility alias solely to preserve existing deployment identities.
OpenRouter endpoint fallbacks are disabled and parameter support is required.

## Existing Gemini configuration

`GEMINI_PROJECTS` remains a JSON array of `{label, project_id, api_key}` records
in `.env`. Blank project IDs are allowed and appear as `null`; no shared-budget
assumption is made. Models and their order are in `models.json`:

1. `gemini-3.8-flash`
2. `gemini-3.5-flash-lite`
3. `gemini-3.1-flash-lite`

Only model names and JSON response modes are needed. No quota manifest or
verification flag is loaded. The previous research is preserved in
[the historical model report](docs/model-research-2026-10-22.md); its old quota
recommendations no longer describe the current application.

## Secrets and persistence

Credentials stay in ignored `.env`; `beatapi.txt` has been scrubbed. Provider JSON
contains variable references, never embedded API keys. Never commit `.env`.

| Setting | Purpose |
|---|---|
| `LLM_CONFIG=llm.json` | Provider/account/model configuration |
| `MODELS_FILE=models.json` | Imported Gemini models and order |
| `GEMINI_PROJECTS` | Local Gemini credentials and optional project IDs |
| `ROUTER_DB=router.sqlite3` | Persistent rotation and provider error state |
| `RETRIES_PER_DEPLOYMENT=2` | Additional same-deployment attempts |
| `COMPLETION_MAX_DEPLOYMENTS=all` | Try the full eligible sequence during recovery; optionally set a positive integer |

Relative paths resolve beside `.env`. The default client finds `.env` at the
repository root, alongside the `ntg_llm` package. `QUOTA_DB` remains a legacy alias for `ROUTER_DB`; the existing database
file is reused without resetting its cursor. Old quota configuration variables
are no longer needed and were removed from the supplied `.env` files.

Use a shared local-filesystem database for processes on one host. On startup, old generic permission stops are migrated to deployment-scoped
access-denial records without resetting the cursor. Explicit enforcement stops
remain global. After
restoring access to a model, remove its row from `unavailable`. Historical usage
rows have no effect; no database deletion or counter reset is required.

## Verification

Tests use mock credentials and mocked provider transports. They cover repeated
rotation without local limits, concurrent selection, persistence, legacy usage
records being ignored, unrestricted local prompt sizes, retries, provider
Retry-After, model unavailability, permission stops, JSON validation, provider
adapters, conversation roles, and clean CLI errors. No live API calls are made.

## Diagnose a provider access denial

A provider 401/403 is distinct from invalid JSON or temporary 503 availability.
Ordinary access denials now disable only the failed deployment and permit fallback.
Only explicit suspension or leaked-key enforcement stops the complete router.
Access errors now include an allowlisted `access_error.reason`, explanation,
and remediation action. Only those safe fields are saved, not raw provider
messages, request headers, error metadata, or credentials.

```powershell
python diagnose.py
```

This reads a global enforcement stop or the most recent deployment access denial
from local state only and makes no live provider request. Older
saved failures cannot reveal an error body that the old implementation discarded.

After investigating or correcting the key/project in the provider console, an
explicit live diagnostic can recheck the exact blocked key/model:

```powershell
python diagnose.py --probe-blocked
```

This sends one `hi` request using only that blocked deployment. It does not
retry, rotate, or fall back to another key. A continuing access denial updates
the safe reason code for that deployment; a temporary error leaves the saved stop in place. A
validated successful response clears only the same saved global stop or
deployment access denial, restoring that deployment to the rotation. A newer concurrent access stop is preserved.
No diagnostic runs automatically during normal chat calls.
