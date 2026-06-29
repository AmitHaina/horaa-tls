class HoraaTLSError(Exception):
    """Base exception for horaa-tls."""
    pass


class BackendError(HoraaTLSError):
    """Raised when an error occurs in the pluggable connection backend."""
    pass


class NetworkError(HoraaTLSError):
    """Raised when a network-level error occurs (e.g., timeout, connection failure)."""
    pass
