"""Domain errors. Services raise these; `app.main` maps them to HTTP responses."""


class AuthenticationError(Exception):
    """Credentials are missing, malformed, expired or wrong."""


class PermissionDeniedError(Exception):
    """Caller is authenticated but lacks the required role."""


class NotFoundError(Exception):
    """Resource does not exist inside the caller's tenant."""


class RateLimitedError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds
