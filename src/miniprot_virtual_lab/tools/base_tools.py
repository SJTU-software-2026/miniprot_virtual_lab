from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """The name of the tool."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """The description of the tool."""
        pass
    
    @abstractmethod
    def get_schema(self) -> Dict[str, Any]:
        """Return the schema of the tool."""
        pass
    
    @abstractmethod
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool logic."""
        pass
