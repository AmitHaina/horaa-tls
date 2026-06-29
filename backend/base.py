from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseBackend(ABC):
    """Abstract Base Class for pluggable request engines."""

    @abstractmethod
    def execute(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes an HTTP request synchronously.
        
        Args:
            request_payload: A dictionary of parameters matching the backend requirements.
            
        Returns:
            A dictionary containing response details.
        """
        pass

    @abstractmethod
    async def execute_async(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes an HTTP request asynchronously.
        
        Args:
            request_payload: A dictionary of parameters matching the backend requirements.
            
        Returns:
            A dictionary containing response details.
        """
        pass
