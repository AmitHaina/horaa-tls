import socket

CHARLES_CONFIGURATION = ("127.0.0.1", 8888, 0.01)
FIDDLER_CONFIGURATION = ("127.0.0.1", 8889, 0.01)


def is_port_open(host: str, port: int, timeout: float = 0.01) -> bool:
    """Checks if a local port is open by attempting a TCP connection."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def is_charles_running() -> bool:
    """Checks if Charles Proxy is running on port 8888."""
    return is_port_open(*CHARLES_CONFIGURATION)


def is_fiddler_running() -> bool:
    """Checks if Fiddler is running on port 8889."""
    return is_port_open(*FIDDLER_CONFIGURATION)


def detect_active_debugging_proxy() -> str:
    """
    Returns proxy URL of the first active debugging proxy detected (Charles or Fiddler).
    Returns None if none are running.
    """
    if is_charles_running():
        return "http://127.0.0.1:8888"
    if is_fiddler_running():
        return "http://127.0.0.1:8889"
    return None
