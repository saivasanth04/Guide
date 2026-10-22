"""Compatibility alias for the packaged client."""

import sys
from ntg_llm import client

sys.modules[__name__] = client
