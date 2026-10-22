"""LiteLLM adapter for an already selected deployment."""

import contextvars
from litellm.router import CustomRoutingStrategyBase
from .errors import RoutingError


class PersistentRoundRobin(CustomRoutingStrategyBase):
    def __init__(self, ledger, deployments):
        self.ledger, self.deployments = ledger, deployments
        self.selection = contextvars.ContextVar("selection", default=None)
        self.model_list = []

    def get_available_deployment(self, model, messages=None, **kwargs):
        selected = self.selection.get()
        if selected is None:
            raise RoutingError(500, "Missing deployment selection")
        return next(d for d in self.model_list if d["model_info"]["id"] == selected.id)

    async def async_get_available_deployment(self, *args, **kwargs):
        return self.get_available_deployment(*args, **kwargs)
