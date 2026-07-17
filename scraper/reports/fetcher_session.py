from __future__ import annotations

import time

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


from scraper.reports.fetcher_types import *  # noqa: F401,F403
from scraper.reports.util import (  # noqa: F401
    _CAPTCHA_MARKERS,
    _DISCLAIMER_MARKERS,
    _LABEL_MAP,
    _LONG_VALUE_KEYS,
    _MAX_CRIME_LEN,
    _PHOTO_HOST_STATE,
    _clean_value,
    _normalize_label,
    _normalize_url,
    photo_state_from_url,
    photo_url_variants,
    extract_dedicated_photo_urls,
)

class FetcherSessionMixin:
    def __init__(
        self,
        delay: float = DEFAULT_DELAY,
        timeout: float = REQUEST_TIMEOUT,
        *,
        cookie_store: Optional[CookieJarStore] = None,
        captcha_queue: Optional[CaptchaQueue] = None,
        use_saved_cookies: bool = True,
    ):
        # delay=0 when the caller (builder) owns rate limiting — avoid double sleeps.
        self.delay = max(0.0, float(delay))
        self.timeout = timeout
        self.cookie_store = cookie_store if cookie_store is not None else CookieJarStore()
        self.captcha_queue = captcha_queue if captcha_queue is not None else CaptchaQueue()
        self.use_saved_cookies = bool(use_saved_cookies)
        self.session = self._make_session()
        if self.use_saved_cookies:
            try:
                self.cookie_store.apply_to_session(self.session)
            except Exception:
                pass


    @staticmethod
    def _make_session() -> Any:
        try:
            from curl_cffi import requests as creq  # type: ignore

            session = creq.Session(impersonate="chrome")
        except Exception:
            session = requests.Session()
            session.headers["User-Agent"] = BROWSER_UA
        session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
        )
        return session


    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


    def _note_captcha_block(
        self,
        url: str,
        *,
        jurisdiction: str = "",
        reason: str = "captcha",
        name: str = "",
    ) -> None:
        try:
            self.captcha_queue.add(
                url, jurisdiction=jurisdiction, reason=reason, name=name
            )
        except Exception:
            pass


    def _persist_cookies(self, url: str) -> None:
        if not self.use_saved_cookies:
            return
        try:
            self.cookie_store.capture_from_session(self.session, url)
        except Exception:
            pass


    def _pace(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)


    def _get(self, url: str, **kwargs: Any) -> Any:
        """GET with automatic verify=False retry on TLS/cert failures.

        Virginia vspsor.com (and some other SOR hosts) fail default CA verify
        on Windows while the site is otherwise reachable — not a block page.
        """
        try:
            return self.session.get(
                url,
                timeout=self.timeout,
                allow_redirects=True,
                **kwargs,
            )
        except Exception as e:
            if kwargs.get("verify", True) is False:
                raise
            msg = str(e).lower()
            if not (
                "ssl" in msg
                or "certificate" in msg
                or "cert" in msg
                or "certificate_verify" in msg
            ):
                raise
            kwargs = {**kwargs, "verify": False}
            return self.session.get(
                url,
                timeout=self.timeout,
                allow_redirects=True,
                **kwargs,
            )


    def _post(self, url: str, data: Dict[str, str], referer: str = "") -> Any:
        headers: Dict[str, str] = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        return self.session.post(
            url,
            data=data,
            timeout=self.timeout,
            allow_redirects=True,
            headers=headers,
        )


