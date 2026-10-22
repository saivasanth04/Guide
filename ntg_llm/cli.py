"""Provider-neutral application boundary. Provider settings live in local config."""

import json
import math
import sys
from functools import wraps
from .errors import RoutingError as LLMError


def cli_entrypoint(function):
    """Keep terminal error formatting out of application chat code."""

    @wraps(function)
    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LLMError as error:
            payload = error.as_dict()
            try:
                delay = float(error.retry_after)
            except (TypeError, ValueError):
                delay = None
            if delay is not None and math.isfinite(delay):
                payload["retry_after"] = max(1, math.ceil(delay))
            print(json.dumps(payload, indent=2), file=sys.stderr)
            raise SystemExit(1) from None

    return run
