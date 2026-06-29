import os
from typing import Any, Dict, List, Optional
from horaa_tls.middleware.base import BaseMiddleware
from horaa_tls.response import Response


def normalize_proxy_url(proxy: str) -> str:
    """
    Normalizes different proxy string formats into a standard http URL.
    Formats supported:
      - 'ip:port' -> 'http://ip:port'
      - 'ip:port:user:pass' -> 'http://user:pass@ip:port'
      - 'http://ip:port' -> 'http://ip:port'
      - 'http://user:pass@ip:port' -> 'http://user:pass@ip:port'
    """
    proxy = proxy.strip()
    if not proxy:
        return ""

    if proxy.startswith("http://") or proxy.startswith("https://") or proxy.startswith("socks5://") or proxy.startswith("socks4://"):
        return proxy

    parts = proxy.split(":")
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    elif len(parts) == 4:
        return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    
    return f"http://{proxy}"


class ProxyRotatorMiddleware(BaseMiddleware):
    """
    Middleware that manages proxy lists, handles automatic proxy rotation
    per request, and performs failover proxy replacement on network errors or anti-bot blocks.
    """

    def __init__(
        self,
        proxies: Optional[List[str]] = None,
        file_path: Optional[str] = None,
        mode: str = "failover",
        max_failovers: int = 5,
    ):
        """
        Args:
            proxies: Explicit list of proxy strings.
            file_path: Absolute or relative path to a proxy file (one proxy per line).
            mode: 'request' (rotate on every request) or 'failover' (rotate on error/block).
            max_failovers: Limit on consecutive failover retries to prevent infinite loops.
        """
        self.proxies: List[str] = []
        self.mode = mode.lower()
        self.max_failovers = max_failovers
        self._index = 0

        # Load proxies
        if proxies:
            self.proxies = [normalize_proxy_url(p) for p in proxies if p.strip()]
        elif file_path and os.path.exists(file_path):
            with open(file_path, "r") as f:
                self.proxies = [
                    normalize_proxy_url(line)
                    for line in f.read().splitlines()
                    if line.strip()
                ]

    def _get_current_proxy(self) -> str:
        if not self.proxies:
            return ""
        return self.proxies[self._index % len(self.proxies)]

    def _rotate(self):
        if self.proxies:
            self._index += 1

    def before_request(self, session, payload: Dict[str, Any]) -> None:
        if not self.proxies:
            return

        # If rotating per request, cycle the index
        if self.mode == "request":
            self._rotate()

        # Inject proxy into payload
        current_proxy = self._get_current_proxy()
        payload["proxyUrl"] = current_proxy

    def after_response(
        self, session, payload: Dict[str, Any], response: Response
    ) -> Optional[Dict[str, Any]]:
        # If mode is failover and the response status code is a block/throttle (403/429)
        if self.mode == "failover" and response.status_code in (403, 429):
            failovers = payload.get("_proxy_failover_count", 0)
            if failovers >= self.max_failovers or failovers >= len(self.proxies):
                return None  # Stop retrying, propagate response

            self._rotate()
            next_payload = payload.copy()
            next_payload["proxyUrl"] = self._get_current_proxy()
            next_payload["_proxy_failover_count"] = failovers + 1
            print(f"[horaa-tls] Proxy blocked ({response.status_code}). Failover to next proxy: {next_payload['proxyUrl']}")
            return next_payload

        return None

    def after_error(
        self, session, payload: Dict[str, Any], error: Exception
    ) -> Optional[Dict[str, Any]]:
        # If execution encounters network or connection failures, attempt a failover proxy
        if self.mode == "failover" and self.proxies:
            failovers = payload.get("_proxy_failover_count", 0)
            if failovers >= self.max_failovers or failovers >= len(self.proxies):
                return None  # Propagate the error

            self._rotate()
            next_payload = payload.copy()
            next_payload["proxyUrl"] = self._get_current_proxy()
            next_payload["_proxy_failover_count"] = failovers + 1
            print(f"[horaa-tls] Network error: {error}. Retrying with next proxy: {next_payload['proxyUrl']}")
            return next_payload

        return None
