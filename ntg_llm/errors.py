"""Sanitized application errors shared by all adapters."""


class RoutingError(Exception):
    def __init__(
        self, status, message, diagnostics=None, retry_after=None, transient=False, failover=False
    ):
        super().__init__(message)
        self.status_code = status
        self.diagnostics = diagnostics or {}
        self.retry_after = retry_after
        self.transient = transient
        self.failover = failover

    def as_dict(self):
        return {
            "error": str(self),
            "status": self.status_code,
            "retry_after": self.retry_after,
            "diagnostics": self.diagnostics,
        }
