"""High-level mugshot scorer with caching."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Union

from scraper.mugshot_ethnicity.backends import EthnicityBackend, create_backend, list_backend_status
from scraper.mugshot_ethnicity.models import FaceEthnicityScore


class BackendUnavailableError(RuntimeError):
    """Raised when no vision backend can be loaded."""


class MugshotEthnicityScorer:
    """Score offender mugshots for ethnicity/race appearance.

    Parameters
    ----------
    backend:
        Backend name (``auto``, ``deepface``, ``clip``, ``mock``) or instance.
    min_file_bytes:
        Skip tiny / broken photo stubs.
    cache:
        Reuse scores for the same path within this process.
    """

    def __init__(
        self,
        backend: Union[str, EthnicityBackend] = "auto",
        *,
        min_file_bytes: int = 1500,
        cache: bool = True,
        auto_install: bool = True,
        log=None,
    ):
        self.min_file_bytes = int(min_file_bytes)
        self._cache_enabled = bool(cache)
        self._cache: Dict[str, FaceEthnicityScore] = {}
        if isinstance(backend, EthnicityBackend):
            self.backend = backend
        else:
            try:
                self.backend = create_backend(
                    str(backend),
                    auto_install=auto_install,
                    log=log,
                )
            except RuntimeError as e:
                raise BackendUnavailableError(str(e)) from e

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", "unknown")

    def score_path(self, photo_path: str) -> FaceEthnicityScore:
        path = (photo_path or "").strip()
        if not path:
            return FaceEthnicityScore(
                photo_path="",
                top_label="unknown",
                top_confidence=0.0,
                backend=self.backend_name,
                face_detected=False,
                error="empty photo path",
            )
        key = str(Path(path).resolve()) if Path(path).exists() else path
        if self._cache_enabled and key in self._cache:
            return self._cache[key]

        p = Path(path)
        if not p.is_file():
            result = FaceEthnicityScore(
                photo_path=path,
                top_label="unknown",
                top_confidence=0.0,
                backend=self.backend_name,
                face_detected=False,
                error="file not found",
            )
        elif p.stat().st_size < self.min_file_bytes:
            result = FaceEthnicityScore(
                photo_path=path,
                top_label="unknown",
                top_confidence=0.0,
                backend=self.backend_name,
                face_detected=False,
                error=f"file too small ({p.stat().st_size} bytes)",
            )
        else:
            result = self.backend.analyze(str(p))

        if self._cache_enabled:
            self._cache[key] = result
        return result

    def score_record(self, record: dict) -> FaceEthnicityScore:
        return self.score_path(str((record or {}).get("photo_path") or ""))

    def clear_cache(self) -> None:
        self._cache.clear()


def get_available_backends() -> Dict[str, bool]:
    return list_backend_status()
