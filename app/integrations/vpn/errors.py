"""Transport-independent provider failures. No raw response text is user-facing."""

import httpx

from app.domain.errors import IntegrationUnavailable


class ProviderError(IntegrationUnavailable):
    pass


class AuthenticationError(ProviderError):
    pass


class PermissionError(ProviderError):
    pass


class NotFound(ProviderError):
    pass


class AlreadyExists(ProviderError):
    pass


class ValidationError(ProviderError):
    pass


class Unavailable(ProviderError):
    pass


class Timeout(ProviderError):
    pass


class AmbiguousOperation(ProviderError):
    pass


def transport_error(exc: httpx.HTTPError, *, mutation: bool) -> ProviderError:
    """Conservative generic HTTP classification; panel semantic envelopes need its real API."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        known = {
            401: AuthenticationError,
            403: PermissionError,
            404: NotFound,
            409: AlreadyExists,
            400: ValidationError,
            422: ValidationError,
        }
        if status in known:
            return known[status]()
        if mutation and status >= 500:
            return AmbiguousOperation()
        return Unavailable()
    if mutation:
        return AmbiguousOperation()
    return Timeout() if isinstance(exc, httpx.TimeoutException) else Unavailable()
