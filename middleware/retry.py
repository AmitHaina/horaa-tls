from typing import Any, Dict, Optional, Tuple, Type
from horaa_tls.middleware.base import BaseMiddleware
from horaa_tls.response import Response
from horaa_tls.exceptions import NetworkError, BackendError


class RetryMiddleware(BaseMiddleware):
    """
    Middleware that intercepts request errors and automatically retries
    failed requests using exponential backoff.
    """

    def __init__(
        self,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        retry_on_status: Tuple[int, ...] = (500, 502, 503, 504),
    ):
        """
        Args:
            max_retries: Maximum number of retry attempts.
            backoff_factor: Multiplier for exponential backoff (delay = factor * 2^attempt).
            retry_on_status: HTTP status codes that should trigger a retry.
        """
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.retry_on_status = retry_on_status

    def after_response(
        self, session, payload: Dict[str, Any], response: Response
    ) -> Optional[Dict[str, Any]]:
        # If the response status code is a retryable server error
        if response.status_code in self.retry_on_status:
            attempt = payload.get("_retry_attempt", 0)
            if attempt < self.max_retries:
                delay = self.backoff_factor * (2 ** attempt)
                print(f"[horaa-tls] Server error status {response.status_code}. Retrying in {delay:.2f}s... (Attempt {attempt + 1}/{self.max_retries})")

                next_payload = payload.copy()
                next_payload["_retry_attempt"] = attempt + 1
                # Delay is applied by the caller (sync: time.sleep, async: asyncio.sleep)
                # so the async event loop is never blocked by a synchronous sleep here.
                next_payload["_retry_delay"] = delay
                return next_payload

        return None

    def after_error(
        self, session, payload: Dict[str, Any], error: Exception
    ) -> Optional[Dict[str, Any]]:
        # Only retry on network/connection/backend errors
        if isinstance(error, (NetworkError, BackendError, ConnectionError, TimeoutError)):
            attempt = payload.get("_retry_attempt", 0)
            if attempt < self.max_retries:
                delay = self.backoff_factor * (2 ** attempt)
                print(f"[horaa-tls] Network exception: {error}. Retrying in {delay:.2f}s... (Attempt {attempt + 1}/{self.max_retries})")

                next_payload = payload.copy()
                next_payload["_retry_attempt"] = attempt + 1
                next_payload["_retry_delay"] = delay
                return next_payload

        return None
