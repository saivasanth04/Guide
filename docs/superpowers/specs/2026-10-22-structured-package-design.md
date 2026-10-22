# Structured completions and package refactor

Approved direction: an installable root ntg_llm package, caller-provided Pydantic
response models, strict JSON validation, and a short main.py example.

## Boundaries
The public client delegates to a completion service. Settings assemble deployments
and a SQLite store; the store exclusively owns transactional rotation state.
The LiteLLM strategy pins a deployment selected by the store. Provider exceptions
are classified separately from the service's retry and fallback policy.

## Contract
Answer remains the default response model and the existing answer/diagnostics
mapping remains compatible. Custom models produce a data/diagnostics mapping;
chat responses expose a typed message.parsed and JSON content for custom models.
Schemas contain only declared object fields. Local JSON Schema and strict
Pydantic validation reject invalid output before application code sees it.
No retries or fallback occur for invalid output. Schema failures before dispatch
return 400. Provider/output failures preserve sanitized error diagnostics.

## Migration and verification
Root module compatibility exports remain available. Secrets/config files and the
SQLite database are unchanged; deployment identities, retry policy, cooldowns and
rotation persistence remain intact. No quota assumptions are reintroduced.
Tests are organized by access, store, recovery, providers, client and structured
output, with shared mocked transports and an automatic live-HTTP blocker.
All verification is offline. Main is executed only against a fake service.
