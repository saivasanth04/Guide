> Historical report: local quota budgets and token reservations described below have since been removed. The current application uses round-robin selection without local rate limits. See README.md.

# Model research and incident report - 2026-10-22

## What the supplied logs establish

`gemini-3.1-flash-lite` returned 503 three times. Those were the initial attempt
and two same-account/model retries, followed by fallback to `gemini-2.5-flash`.
A 503 does not establish retirement, invalid credentials, or exhausted quota.
Google recommends exponential backoff with jitter for transient service errors.
[Google troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)

The next attempt returned a model-access 404. Google restricts 2.5 access to
prior active users; it is not a universal shutdown. Google recommends newer
3.5 Flash-Lite or 3.8 Flash for new projects. 3.1 Flash-Lite remains listed with
a May 7, 2027 shutdown date; 3 Flash Preview has no announced shutdown date.
[Google deprecations](https://ai.google.dev/gemini-api/docs/deprecations)

## Text generation models relevant to this project

Google's pricing page lists free input/output for these standard text models:

| Model | Configuration decision |
|---|---|
| gemini-3.8-flash | Default slot 1 |
| gemini-3.5-flash-lite | Default slot 2 |
| gemini-3.6-flash | Default slot 3 |
| gemini-3.1-flash-lite | Optional older model, no longer default |
| gemini-3-flash-preview | Optional legacy preview, no longer default |
| gemini-2.5-flash / gemini-2.5-flash-lite / gemini-2.5-pro | Legacy access restrictions; excluded from defaults |

This is the text-chat subset, not an exhaustive list of audio, embedding, or
media endpoints. Gemini 4 Argon has no free tier. Availability on an individual
project is not established by a public pricing listing.
[Google pricing](https://ai.google.dev/gemini-api/docs/pricing)

The three new default models support structured text outputs.
[3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash),
[3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite),
[3.6 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash)

## What cannot be established offline

Exact project quotas, key ownership, shared quota groups, and live eligibility
were not queried. Google directs users to active project limits in AI Studio;
quotas are project-scoped, input-token based for TPM, and RPD resets at Pacific
midnight. Published capacity is not a guarantee.
[Google rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)

The old 1000/1500 RPD assumptions were replaced with configurable conservative
local budgets, not claimed provider quotas. No credentialed provider calls,
preflight requests, model discovery, or test generations were made during this
research. Public documentation research and mocked tests do not prove that an
individual key can use a model. Runtime success/failure remains the evidence.

## Changes made

Model order and local budgets live in `models.json`. Transient failures retain
same-deployment retries before fallback. Model-specific 404s persist as local
unavailability and fall through without wasting retries. Permission/suspension
errors still stop globally. Existing quota state is retained, and legacy cursor
positions map to replacement slots. Raw LiteLLM fallback logs are suppressed in
favor of sanitized final diagnostics. New models have no invented pricing data.
