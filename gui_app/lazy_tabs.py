"""Lazy tab host: build tab body on first selection."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class LazyTabHost:
    """CTkTabview wrapper: first selection builds tab body once."""

    def __init__(self, tabview: Any, on_change: Optional[Callable] = None):
        self.tabview = tabview
        self._loaders: Dict[str, Callable] = {}
        self._loaded: Dict[str, Any] = {}
        self._on_change = on_change
        try:
            tabview.configure(command=self._handle_change)
        except Exception:
            pass

    def register(self, name: str, loader: Callable) -> None:
        """loader(parent) builds widgets into parent; return value is stored as controller."""
        self.tabview.add(name)
        self._loaders[name] = loader

    def _handle_change(self, name: Optional[str] = None) -> None:
        try:
            name = name or self.tabview.get()
        except Exception:
            return
        if name not in self._loaded and name in self._loaders:
            parent = self.tabview.tab(name)
            self._loaded[name] = self._loaders[name](parent)
        if self._on_change:
            try:
                self._on_change(name)
            except Exception:
                pass

    def ensure(self, name: str) -> Any:
        """Force-load a tab (e.g. default landing tab)."""
        self._handle_change(name)
        return self._loaded.get(name)

    def get_controller(self, name: str) -> Any:
        return self._loaded.get(name)

    def is_loaded(self, name: str) -> bool:
        return name in self._loaded
