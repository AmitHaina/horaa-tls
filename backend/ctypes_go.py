import ctypes
import json
import asyncio
from typing import Any, Dict, Optional

from horaa_tls.exceptions import BackendError
from horaa_tls.utils.updater import update_if_necessary


class CtypesGoBackend:
    """
    Backend implementation that loads the compiled Go tls-client library
    via ctypes and invokes it in-process.
    """

    _lib = None

    @classmethod
    def get_library(cls):
        """Loads and returns the ctypes Go dynamic library, initializing it on first use."""
        if cls._lib is None:
            try:
                # Retrieve (and download if needed) the precompiled binary
                lib_path = update_if_necessary()
                lib = ctypes.cdll.LoadLibrary(lib_path)

                # Define argtypes and restypes for Go-exported C functions
                lib.request.argtypes = [ctypes.c_char_p]
                lib.request.restype = ctypes.c_char_p

                lib.freeMemory.argtypes = [ctypes.c_char_p]
                lib.freeMemory.restype = ctypes.c_char_p

                lib.getCookiesFromSession.argtypes = [ctypes.c_char_p]
                lib.getCookiesFromSession.restype = ctypes.c_char_p

                lib.addCookiesToSession.argtypes = [ctypes.c_char_p]
                lib.addCookiesToSession.restype = ctypes.c_char_p

                lib.destroySession.argtypes = [ctypes.c_char_p]
                lib.destroySession.restype = ctypes.c_char_p

                lib.destroyAll.argtypes = []
                lib.destroyAll.restype = ctypes.c_char_p

                cls._lib = lib
            except Exception as e:
                raise BackendError(f"Failed to load and initialize Go shared library: {e}")
        return cls._lib

    def _call(self, response_ptr, error_context: str) -> Optional[Dict[str, Any]]:
        """
        Shared response handling for every Go FFI call: decodes the C string pointer,
        parses it as JSON, and always frees the Go-allocated memory afterwards.
        Returns None if the Go library returned a null pointer.
        """
        if not response_ptr:
            return None

        lib = self.get_library()
        res_obj = None
        try:
            response_bytes = ctypes.string_at(response_ptr)
            res_obj = json.loads(response_bytes.decode("utf-8"))
            return res_obj
        except Exception as e:
            raise BackendError(f"Failed to parse Go response ({error_context}): {e}")
        finally:
            # Always call freeMemory to release the C-string allocated by Go FFI
            if isinstance(res_obj, dict) and "id" in res_obj:
                lib.freeMemory(res_obj["id"].encode("utf-8"))

    def _execute_sync(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Wrapper around the ctypes C call to request and free memory in Go."""
        lib = self.get_library()
        # Clean request payload by removing private keys starting with '_' (used for Python middleware state)
        clean_payload = {k: v for k, v in request_payload.items() if not k.startswith("_")}
        payload_bytes = json.dumps(clean_payload).encode("utf-8")

        response_ptr = lib.request(payload_bytes)
        response_data = self._call(response_ptr, "request")
        if response_data is None:
            raise BackendError("Null pointer returned from Go request execution.")
        return response_data

    def execute(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute request synchronously."""
        return self._execute_sync(request_payload)

    async def execute_async(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute request asynchronously by running the blocking ctypes call in an executor."""
        loop = asyncio.get_running_loop()
        # run_in_executor runs the synchronous FFI block in a background thread to prevent GIL stalling
        return await loop.run_in_executor(None, self._execute_sync, request_payload)

    def get_cookies(self, session_id: str, url: str) -> list:
        """Fetch cookies stored in the Go session memory for a given URL."""
        lib = self.get_library()
        payload = json.dumps({"sessionId": session_id, "url": url}).encode("utf-8")
        res_obj = self._call(lib.getCookiesFromSession(payload), "get_cookies")
        return res_obj.get("cookies", []) if res_obj else []

    def add_cookies(self, session_id: str, url: str, cookies: list) -> list:
        """Add cookies into Go session memory for a given URL."""
        lib = self.get_library()
        payload = json.dumps({
            "sessionId": session_id,
            "url": url,
            "cookies": cookies
        }).encode("utf-8")
        res_obj = self._call(lib.addCookiesToSession(payload), "add_cookies")
        return res_obj.get("cookies", []) if res_obj else []

    def destroy_session(self, session_id: str) -> bool:
        """Destroys the session inside Go memory, releasing connections."""
        lib = self.get_library()
        payload = json.dumps({"sessionId": session_id}).encode("utf-8")
        res_obj = self._call(lib.destroySession(payload), "destroy_session")
        return res_obj.get("success", False) if res_obj else False

    def destroy_all_sessions(self) -> bool:
        """Destroys all active sessions inside Go memory."""
        lib = self.get_library()
        res_obj = self._call(lib.destroyAll(), "destroy_all_sessions")
        return res_obj.get("success", False) if res_obj else False