"""Offline LiteLLM catalog metadata and sanitized logging setup."""

import os
import json
import logging
from importlib.resources import files

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm

litellm.suppress_debug_info = True
litellm.turn_off_message_logging = True
_CACHE_PRICES = {
    model: {
        k: v for k, v in entry.items() if k.startswith("cache_") and "cost" in k and v is not None
    }
    for model, entry in json.loads(
        files("litellm")
        .joinpath("model_prices_and_context_window_backup.json")
        .read_text(encoding="utf-8")
    ).items()
    if isinstance(entry, dict)
}


class _HandledRouterLogs(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        # Final failures are surfaced by RoutingError with sanitized diagnostics.
        return not (
            "Error occurred while trying to do fallbacks" in message
            or message.startswith("register_model: model=")
        )


for _logger_name in ("LiteLLM", "LiteLLM Router"):
    logging.getLogger(_logger_name).addFilter(_HandledRouterLogs())
