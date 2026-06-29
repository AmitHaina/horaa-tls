import ctypes
import json
import asyncio
from typing import Any, Dict

from horaa_tls.backend.base import BaseBackend
from horaa_tls.exceptions import BackendError
from horaa_tls.utils.updater import update_if_necessary


class CtypesGoBackend(BaseBackend):
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

    def _execute_sync(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Wrapper around the ctypes C call to request and free memory in Go."""
        lib = self.get_library()
        # Clean request payload by removing private keys starting with '_' (used for Python middleware state)
        clean_payload = {k: v for k, v in request_payload.items() if not k.startswith("_")}
        payload_bytes = json.dumps(clean_payload).encode("utf-8")
        
        # Call Go library to execute request
        response_ptr = lib.request(payload_bytes)
        if not response_ptr:
            raise BackendError("Null pointer returned from Go request execution.")

        try:
            # Read from C string pointer
            response_bytes = ctypes.string_at(response_ptr)
            response_data = json.loads(response_bytes.decode("utf-8"))
        except Exception as e:
            raise BackendError(f"Failed to parse Go response: {e}")
        finally:
            # Always call freeMemory to release the C-string allocated by Go FFI
            if "response_data" in locals() and isinstance(response_data, dict) and "id" in response_data:
                response_id = response_data["id"].encode("utf-8")
                lib.freeMemory(response_id)

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
        response_ptr = lib.getCookiesFromSession(payload)
        
        if not response_ptr:
            return []

        try:
            response_bytes = ctypes.string_at(response_ptr)
            res_obj = json.loads(response_bytes.decode("utf-8"))
            cookies = res_obj.get("cookies", [])
            return cookies
        finally:
            if "res_obj" in locals() and isinstance(res_obj, dict) and "id" in res_obj:
                lib.freeMemory(res_obj["id"].encode("utf-8"))

    def add_cookies(self, session_id: str, url: str, cookies: list) -> list:
        """Add cookies into Go session memory for a given URL."""
        lib = self.get_library()
        payload = json.dumps({
            "sessionId": session_id,
            "url": url,
            "cookies": cookies
        }).encode("utf-8")
        response_ptr = lib.addCookiesToSession(payload)
        
        if not response_ptr:
            return []

        try:
            response_bytes = ctypes.string_at(response_ptr)
            res_obj = json.loads(response_bytes.decode("utf-8"))
            return res_obj.get("cookies", [])
        finally:
            if "res_obj" in locals() and isinstance(res_obj, dict) and "id" in res_obj:
                lib.freeMemory(res_obj["id"].encode("utf-8"))

    def destroy_session(self, session_id: str) -> bool:
        """Destroys the session inside Go memory, releasing connections."""
        lib = self.get_library()
        payload = json.dumps({"sessionId": session_id}).encode("utf-8")
        response_ptr = lib.destroySession(payload)
        
        if not response_ptr:
            return False

        try:
            response_bytes = ctypes.string_at(response_ptr)
            res_obj = json.loads(response_bytes.decode("utf-8"))
            return res_obj.get("success", False)
        finally:
            if "res_obj" in locals() and isinstance(res_obj, dict) and "id" in res_obj:
                lib.freeMemory(res_obj["id"].encode("utf-8"))

    def destroy_all_sessions(self) -> bool:
        """Destroys all active sessions inside Go memory."""
        lib = self.get_library()
        response_ptr = lib.destroyAll()
        
        if not response_ptr:
            return False

        try:
            response_bytes = ctypes.string_at(response_ptr)
            res_obj = json.loads(response_bytes.decode("utf-8"))
            return res_obj.get("success", False)
        finally:
            if "res_obj" in locals() and isinstance(res_obj, dict) and "id" in res_obj:
                lib.freeMemory(res_obj["id"].encode("utf-8"))
