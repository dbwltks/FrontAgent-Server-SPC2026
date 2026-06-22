from copy import deepcopy
from typing import Any


class TaskMemory:
    def __init__(self, variables: dict[str, Any] | None = None):
        self._original = deepcopy(variables or {})
        self._data = deepcopy(variables or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, values: dict[str, Any] | None) -> None:
        if not values:
            return

        for key, value in values.items():
            self.put(key, value)

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)

    def diff(self) -> dict[str, dict[str, Any]]:
        added = {}
        changed = {}
        removed = {}

        for key, value in self._data.items():
            if key not in self._original:
                added[key] = value
            elif self._original[key] != value:
                changed[key] = {
                    "before": self._original[key],
                    "after": value,
                }

        for key, value in self._original.items():
            if key not in self._data:
                removed[key] = value

        return {
            "added": added,
            "changed": changed,
            "removed": removed,
        }