import uuid
import base64
import urllib.parse
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from horaa_tls.backend.ctypes_go import CtypesGoBackend
from horaa_tls.response import build_response, Response
from horaa_tls.exceptions import BackendError, NetworkError
from horaa_tls.middleware.pipeline import MiddlewarePipeline
from horaa_tls.middleware.redirect import RedirectMiddleware
from horaa_tls.middleware.proxy import ProxyRotatorMiddleware
from horaa_tls.fingerprint.user_agent import UserAgentGenerator


class ClientProfile(str, Enum):
    """Preset browser emulation profiles for Go tls-client library."""
    CHROME_103 = "chrome_103"
    CHROME_110 = "chrome_110"
    CHROME_120 = "chrome_120"
    CHROME_133 = "chrome_133"
    FIREFOX_117 = "firefox_117"
    FIREFOX_123 = "firefox_123"
    FIREFOX_133 = "firefox_133"
    SAFARI_16_0 = "safari_16_0"
    SAFARI_IOS_17_0 = "safari_ios_17_0"
    OPERA_90 = "opera_90"


class Session:
    """
    Session object representing a single TLS connection lifecycle, cookies, and parameters.
    Exposes sync and async APIs.
    """

    def __init__(
        self,
        profile: Union[str, ClientProfile] = ClientProfile.CHROME_120,
        proxy: Optional[str] = None,
        proxies: Optional[List[str]] = None,
        proxy_mode: str = "failover",
        header_order: Optional[List[str]] = None,
        pseudo_header_order: Optional[List[str]] = None,
        insecure_skip_verify: bool = False,
        use_mitm_when_active: bool = True,
    ):
        """
        Args:
            profile: Browser emulation profile string or ClientProfile enum.
            proxy: Single proxy URL.
            proxies: Explicit list of proxy URLs for rotation.
            proxy_mode: Proxy rotation strategy - 'failover' or 'request'.
            header_order: Custom sequence list of HTTP header keys.
            pseudo_header_order: Custom sequence list of HTTP/2 pseudo-header keys (starting with ':').
            insecure_skip_verify: Set to True to bypass SSL certificate verification.
            use_mitm_when_active: Set to True to automatically route traffic through local Charles/Fiddler proxies if detected active.
        """
        self.profile = profile.value if isinstance(profile, ClientProfile) else profile
        self.insecure_skip_verify = insecure_skip_verify
        self.use_mitm_when_active = use_mitm_when_active
        
        # Auto-detect local proxy if active and use_mitm_when_active is True
        self.proxy = proxy
        if not self.proxy and self.use_mitm_when_active:
            from horaa_tls.utils.proxy_detector import detect_active_debugging_proxy
            detected = detect_active_debugging_proxy()
            if detected:
                self.proxy = detected
                
        self.session_id = str(uuid.uuid4())
        self.backend = CtypesGoBackend()

        self.header_order = header_order
        self.pseudo_header_order = pseudo_header_order

        # Redirect stop policies (inspected by RedirectMiddleware)
        self.redirect_stop_at: Optional[str] = None
        self.redirect_stop_if_contains: Optional[str] = None

        self.headers = UserAgentGenerator.generate_headers_for_profile(self.profile)
        self.cookies: Dict[str, str] = {}
        self.timeout_seconds: int = 30

        # Initialize the Pluggable Middleware Subsystem
        self.middleware_pipeline = MiddlewarePipeline()

        # Register RetryMiddleware
        from horaa_tls.middleware.retry import RetryMiddleware
        self.retry_middleware = RetryMiddleware()
        self.middleware_pipeline.add(self.retry_middleware)

        # Register Proxy Rotator if proxies/proxy are configured
        if proxies or proxy:
            proxy_list = proxies if proxies else [proxy]
            self.proxy_middleware = ProxyRotatorMiddleware(proxies=proxy_list, mode=proxy_mode)
            self.middleware_pipeline.add(self.proxy_middleware)
        else:
            self.proxy_middleware = None

        # Register Redirection Resolver
        self.redirect_middleware = RedirectMiddleware()
        self.middleware_pipeline.add(self.redirect_middleware)

    def _prepare_payload(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Union[str, bytes, Dict[str, Any]]] = None,
        json_data: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: Optional[int] = None,
        allow_redirects: bool = True,
        is_byte_response: bool = True,
    ) -> Dict[str, Any]:
        """Constructs the JSON request payload expected by the Go shared library FFI."""
        # 1. Format URL with query parameters
        if params:
            url_parts = list(urllib.parse.urlparse(url))
            query = dict(urllib.parse.parse_qsl(url_parts[4]))
            query.update(params)
            url_parts[4] = urllib.parse.urlencode(query)
            url = urllib.parse.urlunparse(url_parts)

        # 2. Merge headers (case-insensitive)
        merged_headers = {k.lower(): v for k, v in self.headers.items()}
        if headers:
            for k, v in headers.items():
                if v is None:
                    merged_headers.pop(k.lower(), None)
                else:
                    merged_headers[k.lower()] = v

        # 3. Format request body
        request_body = ""
        is_byte_request = False
        content_type = None

        if json_data is not None:
            import json
            request_body = json.dumps(json_data)
            content_type = "application/json"
        elif data is not None:
            if isinstance(data, (bytes, bytearray)):
                request_body = base64.b64encode(data).decode("utf-8")
                is_byte_request = True
            elif isinstance(data, dict):
                request_body = urllib.parse.urlencode(data)
                content_type = "application/x-www-form-urlencoded"
            else:
                request_body = str(data)

        if content_type and "content-type" not in merged_headers:
            merged_headers["content-type"] = content_type

        # 4. Merge cookies
        merged_cookies = self.cookies.copy()
        if cookies:
            merged_cookies.update(cookies)
        
        request_cookies = [
            {"name": name, "value": value, "domain": "", "path": "/"}
            for name, value in merged_cookies.items()
        ]

        # 5. Build payload
        payload = {
            "sessionId": self.session_id,
            "tlsClientIdentifier": self.profile,
            "requestMethod": method.upper(),
            "requestUrl": url,
            "requestBody": request_body,
            "isByteRequest": is_byte_request,
            "isByteResponse": is_byte_response,
            "headers": merged_headers,
            "requestCookies": request_cookies,
            "proxyUrl": proxy or self.proxy or "",
            "timeoutSeconds": timeout or self.timeout_seconds,
            "followRedirects": allow_redirects,
            "insecureSkipVerify": self.insecure_skip_verify,
            "withRandomTLSExtensionOrder": True,
        }

        # Inject custom header sequence lists if defined on the Session
        if self.header_order:
            payload["headerOrder"] = self.header_order
        if self.pseudo_header_order:
            payload["pseudoHeaderOrder"] = self.pseudo_header_order

        return payload

    def _sync_cookies(self, response: Response):
        """Syncs the cookies returned by the request back to the session cookies state."""
        if response.cookies:
            self.cookies.update(response.cookies)

    def request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Union[str, bytes, Dict[str, Any]]] = None,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: Optional[int] = None,
        allow_redirects: bool = True,
    ) -> Response:
        """Executes a request synchronously running through the pluggable middleware pipeline."""
        is_byte_response = True  # Always use byte response to prevent character encoding corruption
        
        payload = self._prepare_payload(
            method=method,
            url=url,
            params=params,
            data=data,
            json_data=json,
            headers=headers,
            cookies=cookies,
            proxy=proxy,
            timeout=timeout,
            allow_redirects=allow_redirects,
            is_byte_response=is_byte_response,
        )

        # Run before_request middleware pipeline hooks
        self.middleware_pipeline.execute_before(self, payload)

        while True:
            try:
                # Execute request via backend FFI
                raw_resp = self.backend.execute(payload)
                
                if raw_resp.get("status") == 0:
                    raise BackendError(raw_resp.get("body", "Go Request Execution Failed"))

                response = build_response(raw_resp, is_byte_response=is_byte_response)
                self._sync_cookies(response)
                
                # Run after_response middleware hooks to check for manual redirects or blocks
                next_payload = self.middleware_pipeline.execute_after(self, payload, response)
                if next_payload is not None:
                    payload = next_payload
                    continue

                return response

            except Exception as e:
                # Run after_error middleware hooks to check for retries/proxy failover
                next_payload = self.middleware_pipeline.execute_error(self, payload, e)
                if next_payload is not None:
                    payload = next_payload
                    continue
                # If no middleware handles the error, re-raise it
                raise

    async def request_async(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Union[str, bytes, Dict[str, Any]]] = None,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: Optional[int] = None,
        allow_redirects: bool = True,
    ) -> Response:
        """Executes a request asynchronously running through the pluggable middleware pipeline."""
        is_byte_response = True
        
        payload = self._prepare_payload(
            method=method,
            url=url,
            params=params,
            data=data,
            json_data=json,
            headers=headers,
            cookies=cookies,
            proxy=proxy,
            timeout=timeout,
            allow_redirects=allow_redirects,
            is_byte_response=is_byte_response,
        )

        # Run before_request middleware pipeline hooks
        self.middleware_pipeline.execute_before(self, payload)

        while True:
            try:
                # Execute request asynchronously via backend FFI
                raw_resp = await self.backend.execute_async(payload)

                if raw_resp.get("status") == 0:
                    raise BackendError(raw_resp.get("body", "Go Request Execution Failed"))

                response = build_response(raw_resp, is_byte_response=is_byte_response)
                self._sync_cookies(response)

                # Run after_response middleware hooks
                next_payload = self.middleware_pipeline.execute_after(self, payload, response)
                if next_payload is not None:
                    payload = next_payload
                    continue

                return response

            except Exception as e:
                # Run after_error middleware hooks
                next_payload = self.middleware_pipeline.execute_error(self, payload, e)
                if next_payload is not None:
                    payload = next_payload
                    continue
                raise

    # Helper HTTP method shorthand functions (sync)
    def get(self, url: str, **kwargs) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, data: Optional[Any] = None, json: Optional[Any] = None, **kwargs) -> Response:
        return self.request("POST", url, data=data, json=json, **kwargs)

    def put(self, url: str, data: Optional[Any] = None, json: Optional[Any] = None, **kwargs) -> Response:
        return self.request("PUT", url, data=data, json=json, **kwargs)

    def delete(self, url: str, **kwargs) -> Response:
        return self.request("DELETE", url, **kwargs)

    def patch(self, url: str, data: Optional[Any] = None, json: Optional[Any] = None, **kwargs) -> Response:
        return self.request("PATCH", url, data=data, json=json, **kwargs)

    def options(self, url: str, **kwargs) -> Response:
        return self.request("OPTIONS", url, **kwargs)

    def head(self, url: str, **kwargs) -> Response:
        kwargs.setdefault("allow_redirects", False)
        return self.request("HEAD", url, **kwargs)

    # Shorthand HTTP async method wrappers
    async def get_async(self, url: str, **kwargs) -> Response:
        return await self.request_async("GET", url, **kwargs)

    async def post_async(self, url: str, data: Optional[Any] = None, json: Optional[Any] = None, **kwargs) -> Response:
        return await self.request_async("POST", url, data=data, json=json, **kwargs)

    async def put_async(self, url: str, data: Optional[Any] = None, json: Optional[Any] = None, **kwargs) -> Response:
        return await self.request_async("PUT", url, data=data, json=json, **kwargs)

    async def delete_async(self, url: str, **kwargs) -> Response:
        return await self.request_async("DELETE", url, **kwargs)

    # Lifecycle support
    def get_cookies_from_backend(self, url: str) -> List[Dict[str, Any]]:
        """Queries the Go memory layer for current active cookies on the specified URL."""
        return self.backend.get_cookies(self.session_id, url)

    def add_cookies_to_backend(self, url: str, cookies: List[Dict[str, Any]]):
        """Directly writes cookies to the Go memory layer."""
        self.backend.add_cookies(self.session_id, url, cookies)

    def close(self) -> bool:
        """Destroys the session connection pool and memory on the Go side."""
        return self.backend.destroy_session(self.session_id)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializes the session state into a dictionary.
        """
        data = {
            "profile": self.profile,
            "headers": self.headers,
            "proxy": self.proxy,
            "insecure_skip_verify": self.insecure_skip_verify,
            "use_mitm_when_active": self.use_mitm_when_active,
            "cookies": self.cookies,
            "timeout_seconds": self.timeout_seconds,
            "redirect_stop_at": self.redirect_stop_at,
            "redirect_stop_if_contains": self.redirect_stop_if_contains,
            "header_order": self.header_order,
            "pseudo_header_order": self.pseudo_header_order,
        }
        # Include proxy rotator state if present
        if self.proxy_middleware:
            data["proxy_middleware"] = {
                "proxies": self.proxy_middleware.proxies,
                "mode": self.proxy_middleware.mode,
                "max_failovers": self.proxy_middleware.max_failovers,
                "index": self.proxy_middleware._index,
            }
        return data

    def to_json(self) -> str:
        """
        Serializes the session state to a JSON string.
        """
        import json
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Session':
        """
        Recreates a Session instance from a dictionary.
        """
        session = cls(
            profile=data.get("profile", ClientProfile.CHROME_120),
            proxy=data.get("proxy"),
            insecure_skip_verify=data.get("insecure_skip_verify", False),
            use_mitm_when_active=data.get("use_mitm_when_active", True),
            header_order=data.get("header_order"),
            pseudo_header_order=data.get("pseudo_header_order"),
        )
        session.headers = data.get("headers", session.headers)
        session.cookies = data.get("cookies", {})
        session.timeout_seconds = data.get("timeout_seconds", 30)
        session.redirect_stop_at = data.get("redirect_stop_at")
        session.redirect_stop_if_contains = data.get("redirect_stop_if_contains")

        # Reinstate proxy rotator state if present
        pm_data = data.get("proxy_middleware")
        if pm_data and session.proxy_middleware:
            session.proxy_middleware.proxies = pm_data.get("proxies", [])
            session.proxy_middleware.mode = pm_data.get("mode", "failover")
            session.proxy_middleware.max_failovers = pm_data.get("max_failovers", 5)
            session.proxy_middleware._index = pm_data.get("index", 0)

        return session

    @classmethod
    def from_json(cls, json_str: str) -> 'Session':
        """
        Recreates a Session instance from a JSON string.
        """
        import json
        data = json.loads(json_str)
        return cls.from_dict(data)
