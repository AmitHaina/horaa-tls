import json
import base64
from typing import Any, Dict, List, Optional, Union
from enum import Enum
from horaa_tls.exceptions import NetworkError


class CaseInsensitiveDict(dict):
    """
    A case-insensitive dictionary wrapper, useful for HTTP headers.
    """
    def __init__(self, data=None, **kwargs):
        super().__init__()
        self._store = {}
        if data:
            self.update(data)
        if kwargs:
            self.update(kwargs)

    def __setitem__(self, key: str, value: Any):
        self._store[key.lower()] = (key, value)

    def __getitem__(self, key: str) -> Any:
        return self._store[key.lower()][1]

    def __delitem__(self, key: str):
        del self._store[key.lower()]

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._store

    def __iter__(self):
        return (casedkey for casedkey, value in self._store.values())

    def __len__(self) -> int:
        return len(self._store)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def update(self, other=None, **kwargs):
        if other:
            if hasattr(other, "keys"):
                for k in other.keys():
                    self[k] = other[k]
            else:
                for k, v in other:
                    self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def copy(self):
        return CaseInsensitiveDict(self)

    def items(self):
        return ((casedkey, value) for casedkey, value in self._store.values())

    def keys(self):
        return (casedkey for casedkey, value in self._store.values())

    def values(self):
        return (value for casedkey, value in self._store.values())

    def __repr__(self) -> str:
        return f"CaseInsensitiveDict({dict(self.items())})"


class Protocol(Enum):
    HTTP_1_1 = "HTTP/1.1"
    HTTP_2 = "HTTP/2.0"
    HTTP_3 = "HTTP/3.0"

    @classmethod
    def from_string(cls, value: str) -> Optional['Protocol']:
        if not value:
            return None
        val = value.upper().strip()
        if val in ("H2", "HTTP/2", "HTTP/2.0"):
            return cls.HTTP_2
        if val in ("H1", "HTTP/1", "HTTP/1.1"):
            return cls.HTTP_1_1
        if val in ("HTTP/3", "HTTP/3.0", "QUIC"):
            return cls.HTTP_3
            
        for member in cls:
            if member.value.upper() == val:
                return member
        return None


class Response:
    """
    Unified Response object mimicking requests/httpx API.
    """
    def __init__(self):
        self.url: str = ""
        self.status_code: int = 0
        self.headers: CaseInsensitiveDict = CaseInsensitiveDict()
        self.cookies: Dict[str, str] = {}
        self.history: List['Response'] = []
        self.used_protocol: Optional[Protocol] = None
        
        self._content: bytes = b""
        self._text: Optional[str] = None

    @property
    def content(self) -> bytes:
        """Raw response body as bytes."""
        return self._content

    @property
    def text(self) -> str:
        """Response body as string, decoded with UTF-8."""
        if self._text is None:
            try:
                self._text = self._content.decode("utf-8", errors="replace")
            except Exception:
                self._text = ""
        return self._text

    def json(self, **kwargs) -> Any:
        """Parses the response body as JSON."""
        return json.loads(self.text, **kwargs)

    def raise_for_status(self):
        """Raises a NetworkError if HTTP status code represents a client or server error."""
        if 400 <= self.status_code < 600:
            raise NetworkError(f"HTTP Error {self.status_code} for url: {self.url}")

    def __repr__(self) -> str:
        return f"<Response [{self.status_code}]>"


def build_response(raw_resp: Dict[str, Any], is_byte_response: bool = False) -> Response:
    """
    Factory function to build a Response object from the backend raw response dictionary.
    """
    response = Response()
    response.url = raw_resp.get("target", "")
    response.status_code = raw_resp.get("status", 0)
    response.used_protocol = Protocol.from_string(raw_resp.get("usedProtocol"))

    # Map headers to CaseInsensitiveDict
    headers_dict = CaseInsensitiveDict()
    raw_headers = raw_resp.get("headers", {})
    for key, values in raw_headers.items():
        # Go tls-client headers return values as a list. We join or pick the first one.
        if isinstance(values, list):
            headers_dict[key] = values[0] if len(values) == 1 else ", ".join(values)
        else:
            headers_dict[key] = str(values)
    response.headers = headers_dict

    # Map body content
    raw_body = raw_resp.get("body", "")
    if is_byte_response and isinstance(raw_body, str):
        if raw_body.startswith("data:") and "," in raw_body:
            try:
                base64_str = raw_body.split(",", 1)[1]
                response._content = base64.b64decode(base64_str)
            except Exception:
                response._content = raw_body.encode("utf-8")
        else:
            try:
                response._content = base64.b64decode(raw_body)
            except Exception:
                response._content = raw_body.encode("utf-8")
    else:
        response._content = raw_body.encode("utf-8") if isinstance(raw_body, str) else b""

    # The Go tls-client returns cookies as a map ({name: value}); handle list form too.
    raw_cookies = raw_resp.get("cookies", {})
    if isinstance(raw_cookies, dict):
        response.cookies.update(raw_cookies)
    elif isinstance(raw_cookies, list):
        for cookie in raw_cookies:
            if isinstance(cookie, dict) and "name" in cookie and "value" in cookie:
                response.cookies[cookie["name"]] = cookie["value"]

    return response
