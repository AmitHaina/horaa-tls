# 🌌 Horaa TLS
*State-of-the-art in-process browser fingerprint emulation and HTTP client for Python.*

`horaa-tls` is a high-performance HTTP client designed to evade anti-bot security layers (such as Cloudflare Turnstile, Akamai, Imperva, and DataDome). By interfacing directly with a precompiled Go-based BoringSSL networking backend via a ctypes FFI wrapper, it maintains a footprint indistinguishable from a real web browser at both the TLS socket and HTTP/2 layer.

---

## 💡 Why Horaa TLS?

*   **Zero External Dependencies**: Automatically detects your OS and architecture, downloads the matching precompiled Go libraries, and initializes everything dynamically without requiring third-party pip dependencies.
*   **Cryptographic Emulation**: Leverages preset browser profiles (Chrome 133, Firefox 133, etc.) to negotiate matching TLS extensions, cipher suites, key share curves, and HTTP/2 settings.
*   **Aligned User-Agents & Client Hints**: Keeps HTTP/2 Client Hints (`Sec-Ch-Ua`, `Sec-Ch-Ua-Mobile`, `Sec-Ch-Ua-Platform`) perfectly aligned with the selected browser TLS version to prevent anti-bot detection signals.
*   **Decoupled Middleware Hooks**: Register asynchronous or synchronous middleware layers (such as rotators and retries) directly in the request-response cycle.

---

## 🚀 Quick Start (The One-Minute Tour)

Initialize a session mimicking a Chrome 133 browser:

```python
from horaa_tls import Session, ClientProfile

# Create a stateful, browser-emulating session
session = Session(profile=ClientProfile.CHROME_133)

try:
    # Perform a request (headers, JA3/JA4, and Client Hints are automatically injected)
    response = session.get("https://httpbingo.org/get")
    print(f"Status Code: {response.status_code}")
    print(response.json())
finally:
    # Always close the session to release low-level FFI memory allocations
    session.close()
```

---

## 🛠️ Core Concepts & Advanced Guide

### 🧬 Aligned Browser Profiles
`horaa-tls` currently offers pre-configured emulation profiles:

*   **Chrome Series**: `chrome_103`, `chrome_110`, `chrome_120`, `chrome_133`
*   **Firefox Series**: `firefox_117`, `firefox_123`, `firefox_133`
*   **Safari Series**: `safari_16_0`, `safari_ios_17_0`
*   **Opera Series**: `opera_90`

### 🏗️ Stateful Middleware Pipeline
You can easily intercept, modify, or retry requests using the built-in middleware engine. Registering rotators or exponential backoffs is straightforward:

```python
from horaa_tls import Session, ClientProfile
from horaa_tls.middleware.proxy import ProxyRotatorMiddleware
from horaa_tls.middleware.retry import RetryMiddleware

session = Session(profile=ClientProfile.CHROME_133)

# 1. State-aware proxy rotator with failover recovery
proxies = ["http://proxy1.example.com:8080", "http://proxy2.example.com:8080"]
session.middleware_pipeline.register(
    ProxyRotatorMiddleware(proxies=proxies, mode="failover", max_failovers=3)
)

# 2. Exponential backoff retry handler for transport/network drops
session.middleware_pipeline.register(
    RetryMiddleware(max_retries=3, backoff_factor=2.0, status_forcelist=[500, 502, 503, 504])
)

# Request executes automatically through the middleware pipeline
res = session.get("https://httpbingo.org/get")
session.close()
```

### 📦 Session Snapshots (Persistence)
Export and restore session states (cookies, custom headers, active proxies, and middleware indicators) to distribute scraper instances across servers or queues:

```python
# Save current state
session_state_json = session.to_json()

# Recreate an identical session in a different worker/process
restored_session = Session.from_json(session_state_json)
```

### 🍪 Direct FFI Cookie Management
Interact directly with the Go-layer cookie jar for fine-grained token/session management:

```python
# Read active cookies stored in the Go memory layer
cookies = session.get_cookies_from_backend("https://example.com")

# Inject cookies directly into the Go-layer FFI engine
session.add_cookies_to_backend("https://example.com", [
    {"name": "session_token", "value": "token_value", "domain": ".example.com", "path": "/"}
])
```
