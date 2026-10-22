"""Default model configuration and legacy identity migration."""

ENFORCEMENT_REASONS = {"CONSUMER_SUSPENDED", "API_KEY_LEAKED"}

MODELS = ("gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
LEGACY_REPLACEMENTS = dict(
    zip(("gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-2.5-flash"), MODELS)
)

# Preserve the cursor when replacing the former third model slot.
LEGACY_REPLACEMENTS["gemini-3.6-flash"] = "gemini-3.1-flash-lite"
