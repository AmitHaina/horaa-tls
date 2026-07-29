import copy
import urllib.parse
from typing import Any, Dict, Optional, List
from horaa_tls.middleware.base import BaseMiddleware
from horaa_tls.response import Response
from horaa_tls.exceptions import NetworkError


class RedirectMiddleware(BaseMiddleware):
    """
    Middleware that intercept 3xx response status codes and manually
    resolves redirects, preventing irregular client redirection behaviors.
    """

    def __init__(self, max_redirects: int = 20):
        self.max_redirects = max_redirects

    def before_request(self, session, payload: Dict[str, Any]) -> None:
        # Save user's original followRedirects preference
        payload["_original_follow_redirects"] = payload.get("followRedirects", True)
        # Disable Go's automatic redirection handling so Python can resolve them manually
        payload["followRedirects"] = False

    def after_response(
        self, session, payload: Dict[str, Any], response: Response
    ) -> Optional[Dict[str, Any]]:
        # If user explicitly set allow_redirects=False, do not follow redirects
        if not payload.get("_original_follow_redirects", True):
            response.history = payload.get("_redirect_history", [])
            return None

        # If the status code is not in the redirect range, assign history and terminate
        if not (300 <= response.status_code < 400):
            response.history = payload.get("_redirect_history", [])
            return None

        # Check for Location header
        location = response.headers.get("Location")
        if not location:
            response.history = payload.get("_redirect_history", [])
            return None

        # Standard-compliant URL joining (fixes relative and absolute url parsing bugs)
        request_url = payload["requestUrl"]
        new_url = urllib.parse.urljoin(request_url, location)

        # Check stop conditions if specified on the session
        stop_at = getattr(session, "redirect_stop_at", None)
        stop_if_contains = getattr(session, "redirect_stop_if_contains", None)

        if stop_at and new_url == stop_at:
            response.history = payload.get("_redirect_history", [])
            return None
        if stop_if_contains and stop_if_contains in new_url:
            response.history = payload.get("_redirect_history", [])
            return None

        # Construct a fully isolated payload for the next hop. deepcopy ensures nested
        # mutable values (headers dict, requestCookies list, redirect history) are not
        # shared with the original payload and mutated inadvertently.
        next_payload = copy.deepcopy(payload)
        next_payload["requestUrl"] = new_url

        # Operate on the history stored in the copied payload so it stays isolated.
        history: List[Response] = next_payload.setdefault("_redirect_history", [])
        if len(history) >= self.max_redirects:
            raise NetworkError(f"Max redirects exceeded (limit: {self.max_redirects})")

        # Append current response to redirect history
        history.append(response)

        # For 301, 302, and 303, standard browser behavior converts method to GET and clears body
        if response.status_code in (301, 302, 303):
            next_payload["requestMethod"] = "GET"
            next_payload["requestBody"] = ""
            next_payload["isByteRequest"] = False
            
            # Remove content headers
            if "headers" in next_payload:
                headers = next_payload["headers"]
                headers_to_remove = [k for k in headers.keys() if k.lower() in ("content-type", "content-length")]
                for k in headers_to_remove:
                    headers.pop(k)

        return next_payload
