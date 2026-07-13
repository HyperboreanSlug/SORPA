"""Jurisdiction report fetcher (HTML, demographics, photos)."""
from __future__ import annotations

# Pull all util names into this module namespace (including _private constants).
import scraper.reports.util as _reports_util

for _name in dir(_reports_util):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_reports_util, _name)
del _name, _reports_util

# Typing / common names used in signatures (also provided by util loop)
from typing import Any, Dict, List, Optional, Set, Tuple  # noqa: E402
from pathlib import Path  # noqa: E402


class ReportFetcher:
    """HTTP client that scrapes demographic fields from report URLs."""

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

    def fetch_demographics(
        self,
        report_url: str,
        save_html: bool = False,
        html_dir: Optional[Path] = None,
        jurisdiction: str = "UNK",
    ) -> Dict[str, Any]:
        """
        Fetch a report URL and return extracted fields.

        When save_html=True, writes the response body under html_dir and sets
        result['report_html_path'] to the relative/local path.
        """
        result: Dict[str, Any] = {
            "report_url": report_url,
            "report_fetch_ok": False,
            "report_fetch_status": None,
        }
        report_url = _normalize_url(str(report_url or ""))
        if not report_url.lower().startswith("http://") and not report_url.lower().startswith(
            "https://"
        ):
            result["report_fetch_status"] = "invalid_url"
            return result
        result["report_url"] = report_url
        original_url = report_url

        try:
            # Texas SOR: try JSON detail endpoint when rapsheet HTML is a JS shell
            tx_json = self._try_texas_json(report_url)
            if tx_json is not None and (
                tx_json.get("race") or tx_json.get("ethnicity") or tx_json.get("gender")
            ):
                if save_html and html_dir is not None:
                    try:
                        resp = self._get(report_url)
                        path, photo_path = self._save_html(
                            resp.content,
                            report_url,
                            html_dir,
                            jurisdiction,
                            resp.url,
                            download_images=True,
                        )
                        if path:
                            result["report_html_path"] = path
                            result["report_final_url"] = resp.url
                        if photo_path:
                            result["photo_path"] = photo_path
                    except Exception:
                        pass
                result.update(tx_json)
                result["report_fetch_ok"] = True
                result["report_fetch_status"] = result.get("report_fetch_status") or 200
                self._pace()
                return result

            # Do NOT strip disclaimer gateways early — land on agree page, POST, then detail.
            resp, used_url = self._get_with_https_fallback(report_url)
            result["report_fetch_status"] = resp.status_code
            result["report_final_url"] = getattr(resp, "url", used_url) or used_url

            if resp.status_code >= 400:
                result["report_block_reason"] = self._classify_block(resp)
                br = result["report_block_reason"] or ""
                if "captcha" in br or "waf" in br:
                    self._note_captcha_block(
                        report_url, jurisdiction=jurisdiction, reason=br
                    )
                    result["needs_manual_captcha"] = True
                self._pace()
                return result

            # Re-apply domain cookies (in case store was updated mid-run)
            if self.use_saved_cookies:
                try:
                    self.cookie_store.apply_to_session(self.session, report_url)
                except Exception:
                    pass

            # Click through Conditions / disclaimer forms
            passed = self._click_through_disclaimers(resp, max_hops=4)
            if passed is not None:
                resp = passed
                result["report_final_url"] = getattr(resp, "url", result["report_final_url"])
                result["report_fetch_status"] = resp.status_code
                result["disclaimer_passed"] = True
                self._persist_cookies(result.get("report_final_url") or report_url)
                if resp.status_code >= 400:
                    result["report_block_reason"] = self._classify_block(resp)
                    br = result["report_block_reason"] or ""
                    if "captcha" in br or "waf" in br:
                        self._note_captcha_block(
                            report_url, jurisdiction=jurisdiction, reason=br
                        )
                        result["needs_manual_captcha"] = True
                    self._pace()
                    return result
                # After agreement, re-fetch original detail URL (CO JSF, WI terms, etc.)
                resp = self._refetch_detail_if_needed(resp, original_url)
                result["report_final_url"] = getattr(resp, "url", result["report_final_url"])
                result["report_fetch_status"] = getattr(resp, "status_code", 200)

            # Maine SOR: national step3 → step4 more-info
            me_resp = self._try_maine_step4(resp, original_url)
            if me_resp is not None:
                resp = me_resp
                result["report_final_url"] = getattr(resp, "url", result["report_final_url"])

            self._pace()

            content_type = (resp.headers.get("Content-Type") or "").lower()
            raw_bytes = resp.content

            if save_html and html_dir is not None:
                path, photo_path = self._save_html(
                    raw_bytes,
                    report_url,
                    html_dir,
                    jurisdiction,
                    result["report_final_url"],
                    download_images=True,
                )
                if path:
                    result["report_html_path"] = path
                if photo_path:
                    result["photo_path"] = photo_path

            if "json" in content_type:
                try:
                    data = resp.json()
                    result.update(self._from_json_blob(data))
                    result["report_fetch_ok"] = self._has_demographics(result)
                    return result
                except ValueError:
                    pass

            text = raw_bytes.decode("utf-8", errors="replace")
            block = self._page_block_reason(text)
            if block and ("captcha" in block or "waf" in block):
                self._note_captcha_block(
                    result.get("report_final_url") or report_url,
                    jurisdiction=jurisdiction,
                    reason=block,
                )
                result["needs_manual_captcha"] = True
                result["report_block_reason"] = block
                result["report_fetch_status"] = f"blocked:{block}"
                self._pace()
                return result
            if block:
                result["report_block_reason"] = block
            extracted = self._from_html(text, base_url=result["report_final_url"])
            result.update(extracted)
            # AL iCrimewatch / WatchSystems: pull dedicated /pictures/ mugshot URL
            # from the page so _ensure_photo can archive under …/photos/ even when
            # NSOPW imageUri is empty or points at a host alias.
            if not result.get("photo_url"):
                dedicated = extract_dedicated_photo_urls(
                    text, base_url=str(result.get("report_final_url") or report_url)
                )
                if dedicated:
                    result["photo_url"] = dedicated[0]
            result["report_fetch_ok"] = self._has_demographics(result)
            if resp.status_code == 200 and len(text) > 500:
                result["report_page_fetched"] = True
            if not result["report_fetch_ok"] and block:
                result["report_fetch_status"] = f"blocked:{block}"
            if result.get("report_fetch_ok"):
                final_u = result.get("report_final_url") or report_url
                self._persist_cookies(final_u)
                try:
                    self.captcha_queue.remove_url(report_url)
                    self.captcha_queue.remove_url(final_u)
                except Exception:
                    pass
            return result
        except Exception as e:
            result["report_fetch_status"] = f"error:{type(e).__name__}"
            result["report_error"] = str(e)[:300]
            self._pace()
            return result

    def _pace(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)

    def _get(self, url: str, **kwargs: Any) -> Any:
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

    def _get_with_https_fallback(self, url: str) -> Tuple[Any, str]:
        """Try URL as-is; on http timeout/SSL issues, retry https / www / verify=False."""
        url = _normalize_url(url)
        candidates = [url]
        if url.startswith("http://"):
            candidates.append("https://" + url[len("http://") :])
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if host and not host.startswith("www."):
            candidates.append(
                urlunparse(parsed._replace(netloc="www." + host, scheme="https"))
            )
        if parsed.scheme == "http":
            candidates.append(urlunparse(parsed._replace(scheme="https")))

        last_err: Optional[Exception] = None
        for cand in candidates:
            try:
                return self._get(cand), cand
            except Exception as e:
                last_err = e
                # TLS / cert failures common on some SOR hosts
                try:
                    resp = self.session.get(
                        cand,
                        timeout=self.timeout,
                        allow_redirects=True,
                        verify=False,
                    )
                    return resp, cand
                except Exception as e2:
                    last_err = e2
                    continue
        if last_err:
            raise last_err
        raise RuntimeError(f"Failed to fetch {url}")

    def _click_through_disclaimers(self, resp: Any, max_hops: int = 3) -> Optional[Any]:
        """
        If the response is a Conditions-of-Use / disclaimer gate, POST the agree
        form (checkbox + Continue) and follow redirects to the real report.

        Handles iCrimeWatch / OffenderWatch / sheriffalerts / communitynotification
        patterns like:
          <form method="post">
            <input type="hidden" name="fwd" value="...">
            <input type="checkbox" name="agree" value="1">
            <input type="submit" name="continue" value="Continue">
          </form>
        """
        current = resp
        any_passed = False
        for _ in range(max_hops):
            text = getattr(current, "text", None) or ""
            if not self._looks_like_disclaimer(text, getattr(current, "url", "") or ""):
                break
            next_resp = self._submit_disclaimer_form(current)
            if next_resp is None:
                break
            any_passed = True
            current = next_resp
            # Stop if we still look stuck on the same disclaimer
            if self._looks_like_disclaimer(
                getattr(current, "text", None) or "",
                getattr(current, "url", "") or "",
            ):
                # One more attempt only if form still present
                continue
            break
        return current if any_passed else None

    @staticmethod
    def _looks_like_disclaimer(text: str, url: str = "") -> bool:
        low = (text or "").lower()
        url_l = (url or "").lower()
        if "cap_office_disclaimer" in url_l or "disclaimer.php" in url_l:
            return True
        if "search-agreement" in url_l or "/terms" in url_l or "agreement.jsf" in url_l:
            return True
        if 'name="agree"' in low or "name='agree'" in low or 'id="agree"' in low:
            if re.search(r"continue|i agree|terms\s*&\s*conditions|disclaimer", low):
                return True
        if "you must agree to the terms" in low:
            return True
        if "you must click on the" in low and "continue" in low and "disclaimer" in low:
            return True
        if "before entering the web site" in low and "agree" in low:
            return True
        if "i agree" in low and "i do not agree" in low:
            return True
        if "acceptform" in low and "submit" in low and "agreement" in low:
            return True
        # Form present with agree checkbox + continue submit
        if re.search(
            r"<form[^>]*>[\s\S]{0,4000}agree[\s\S]{0,2000}continue[\s\S]{0,500}</form>",
            low,
        ):
            return True
        return False

    def _refetch_detail_if_needed(self, resp: Any, original_url: str) -> Any:
        """
        After accepting terms, some sites land on a search home. Re-request the
        original offender detail URL with the new session cookie.
        """
        text = getattr(resp, "text", None) or ""
        if self._has_demographics(self._from_html(text)):
            return resp
        orig = _normalize_url(original_url)
        if not orig:
            return resp
        try:
            again, _ = self._get_with_https_fallback(orig)
            if self._has_demographics(self._from_html(getattr(again, "text", "") or "")):
                return again
            # Prefer page that at least mentions race and is not still a terms form
            low = (getattr(again, "text", "") or "").lower()
            if "race" in low and not self._looks_like_disclaimer(low, getattr(again, "url", "")):
                return again
        except Exception:
            pass
        return resp

    def _try_maine_step4(self, resp: Any, original_url: str) -> Optional[Any]:
        """Maine SOR step3 page links to step4 for full detail."""
        page_url = getattr(resp, "url", "") or original_url or ""
        if "maine.gov" not in page_url.lower() and "maine.gov" not in (original_url or "").lower():
            return None
        html = getattr(resp, "text", None) or ""
        if "step3" not in page_url.lower() and "step3" not in html.lower():
            # still try if form posts to step4
            if "step4" not in html.lower():
                return None
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return None
        for form in soup.find_all("form"):
            action = (form.get("action") or "").lower()
            if "step4" not in action and not any(
                (i.get("value") or "").lower().startswith("request more")
                for i in form.find_all("input")
            ):
                continue
            post_url = urljoin(page_url, form.get("action") or page_url)
            data: Dict[str, str] = {}
            for inp in form.find_all("input"):
                name = inp.get("name")
                if not name:
                    continue
                typ = (inp.get("type") or "text").lower()
                val = inp.get("value") or ""
                if typ == "submit":
                    if "request" in val.lower() or "more" in val.lower():
                        data[name] = val
                elif typ != "checkbox":
                    data[name] = val
            if not data:
                continue
            try:
                return self._post(post_url, data=data, referer=page_url)
            except Exception:
                continue
        return None

    def _submit_disclaimer_form(self, resp: Any) -> Optional[Any]:
        """Find agree/accept form on page and POST it. Returns new response or None."""
        html = getattr(resp, "text", None) or ""
        page_url = getattr(resp, "url", "") or ""
        if not html or not page_url:
            return None
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return None

        form = self._find_disclaimer_form(soup)
        if form is None:
            return None

        action = urljoin(page_url, form.get("action") or page_url)
        data = self._build_disclaimer_post_data(form, page_url)
        if not data:
            return None

        try:
            return self._post(action, data=data, referer=page_url)
        except Exception:
            return None

    @staticmethod
    def _find_disclaimer_form(soup: BeautifulSoup) -> Any:
        """Prefer forms with agree/accept/continue controls."""
        candidates = []
        for form in soup.find_all("form"):
            inputs = form.find_all("input")
            blob = " ".join(
                f"{(i.get('name') or '')} {(i.get('id') or '')} {(i.get('value') or '')} "
                f"{(i.get('type') or '')}"
                for i in inputs
            ).lower()
            score = 0
            if re.search(r"\bagree\b|\baccept\b|\bterms\b", blob):
                score += 2
            if re.search(r"\bcontinue\b|\bsubmit\b|\bproceed\b|\benter\b", blob):
                score += 2
            if "checkbox" in blob:
                score += 1
            if "fwd" in blob or "disc" in blob:
                score += 1
            if "i agree" in blob:
                score += 3
            if "acceptform" in blob or "submitlogin" in blob.replace(":", ""):
                score += 2
            if score >= 2:
                candidates.append((score, form))
        if not candidates:
            # Fallback: any form containing an agree-named control
            for form in soup.find_all("form"):
                for inp in form.find_all("input"):
                    name = (inp.get("name") or inp.get("id") or "").lower()
                    val = (inp.get("value") or "").lower()
                    if name in ("agree", "accept", "iagree", "chkagree", "terms", "tos"):
                        return form
                    if "i agree" in val and "not" not in val:
                        return form
            return None
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]

    @staticmethod
    def _build_disclaimer_post_data(form: Any, page_url: str = "") -> Dict[str, str]:
        """Build POST body: hidden fields + checked agree + continue submit."""
        data: Dict[str, str] = {}
        submit_picked = False

        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            val = inp.get("value")
            if val is None:
                val = ""
            else:
                val = str(val)

            if typ == "hidden":
                data[name] = val
                continue

            if typ in ("checkbox", "radio"):
                blob = f"{name} {val} {inp.get('id') or ''}".lower()
                # Always check terms/agree boxes
                if re.search(r"agree|accept|terms|confirm|license|consent", blob):
                    data[name] = val or "1"
                continue

            if typ == "submit":
                blob = f"{name} {val}".lower()
                # Prefer affirmative agree; skip "I do not agree" / decline
                if re.search(r"do\s*not|don't|disagree|decline|cancel|no\b", blob):
                    continue
                if re.search(
                    r"continue|accept|i agree|agree|submit|enter|yes|proceed|ok",
                    blob,
                ):
                    if not submit_picked:
                        data[name] = val or "Continue"
                        submit_picked = True
                continue

            if typ in ("button", "image", "reset", "file"):
                continue

            # Rare text fields
            if name.lower() in ("agree", "accept"):
                data[name] = val or "1"

        # Ensure agree + continue exist (WatchSystems pattern)
        keys_l = {k.lower() for k in data}
        if "agree" not in keys_l and not any(
            re.search(r"agree|accept|terms", k, re.I) for k in data
        ):
            data["agree"] = "1"
        if not submit_picked and "continue" not in keys_l:
            data["continue"] = "Continue"

        # If form action is empty POST to page URL, keep query `fwd` when missing in body
        if page_url and "fwd" not in data:
            try:
                qs = parse_qs(urlparse(page_url).query)
                fwd = (qs.get("fwd") or [None])[0]
                if fwd:
                    data["fwd"] = fwd
            except Exception:
                pass

        return data

    @staticmethod
    def _resolve_gateway_url(url: str) -> str:
        """Decode sheriffalerts/icrimewatch `fwd=` base64 payloads when present."""
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            fwd = (qs.get("fwd") or [None])[0]
            if not fwd:
                return url
            pad = "=" * (-len(fwd) % 4)
            target = base64.b64decode(fwd + pad).decode("utf-8", errors="replace").strip()
            if target.startswith("http://") or target.startswith("https://"):
                return target
        except Exception:
            pass
        return url

    @staticmethod
    def _has_demographics(data: Dict[str, Any]) -> bool:
        race = (data.get("race") or "").strip()
        # "Unknown" still counts as extracted race field from some states (SC)
        if race:
            return True
        if data.get("ethnicity"):
            return True
        gender = (data.get("gender") or "").strip().lower()
        if gender and gender not in ("minor", "n/a", "unknown", "unk"):
            if data.get("height") or data.get("hair_color") or data.get("eye_color"):
                return True
            # gender alone is weak but useful if paired with DOB/age from report
            if data.get("date_of_birth") or data.get("age"):
                return True
        return bool(data.get("height") and data.get("hair_color"))

    @staticmethod
    def _classify_block(resp: Any) -> str:
        text = (getattr(resp, "text", None) or "")[:2000].lower()
        if resp.status_code in (403, 429):
            if any(m in text for m in _CAPTCHA_MARKERS):
                return "captcha_or_waf"
            return f"http_{resp.status_code}"
        return f"http_{resp.status_code}"

    @staticmethod
    def _page_block_reason(text: str) -> Optional[str]:
        low = text.lower()
        # Real demographic content? Then ignore passive captcha widgets in footers.
        has_demo_signal = bool(
            re.search(r"(?:^|>|\b)race\s*:", low)
            or re.search(r"<td[^>]*>\s*white\s*</td>", low)
            or ("height" in low and "weight" in low and "hair" in low)
        )
        if any(m in low for m in _CAPTCHA_MARKERS):
            # Interactive captcha wall (no offender fields yet)
            if not has_demo_signal:
                if "g-recaptcha" in low or "hcaptcha" in low or "cf-turnstile" in low:
                    return "captcha"
                if "sex offender recaptcha" in low or "complete the captcha" in low:
                    return "captcha"
                if "datadome" in low:
                    return "waf_datadome"
        if not has_demo_signal:
            if "you must agree" in low or "must agree to the terms" in low:
                return "disclaimer_gate"
            if "cap_office_disclaimer" in low:
                return "disclaimer_gate"
        return None
    # Bytes: reject clear stubs; mugshot selection uses a higher bar separately.
    MIN_PHOTO_BYTES = 80
    MIN_PRIMARY_PHOTO_BYTES = 2000

    def download_photo(
        self,
        photo_url: str,
        photo_dir: Path,
        *,
        referer: str = "",
        stem: str = "",
        min_bytes: Optional[int] = None,
        reject_gif: bool = False,
    ) -> Optional[str]:
        """
        Download a single photo URL into photo_dir.
        Returns path relative to cwd when possible.

        Retries with verify=False on TLS/certificate failures (common on some
        state SOR hosts with curl_cffi / incomplete CA stores on Windows).

        SC DisplayImage: tries Thumb=true/false variants when the first response
        is empty, HTML, or a non-image GIF stub.
        """
        base = _normalize_url((photo_url or "").strip())
        if not base.lower().startswith(("http://", "https://")):
            return None
        low_u = base.lower()
        # No photo on record (SC ImageId=0; FL empty CallImage?imgID=)
        try:
            from scraper.mugshot_ethnicity.photo_quality import (
                url_has_empty_image_id,
                url_looks_like_chrome,
            )

            if url_has_empty_image_id(base) or url_looks_like_chrome(base):
                return None
        except Exception:
            if "imgid=0" in low_u or "imageid=0" in low_u or "image_id=0" in low_u:
                return None
        min_sz = self.MIN_PHOTO_BYTES if min_bytes is None else int(min_bytes)
        try:
            photo_dir = Path(photo_dir)
            photo_dir.mkdir(parents=True, exist_ok=True)
            # Stable stem from the *original* URL so variants share one file
            key = stem or sha1(base.encode("utf-8", errors="replace")).hexdigest()[:16]
            # Skip if already have a solid file for this stem
            for existing in photo_dir.glob(f"{key}.*"):
                if existing.is_file() and existing.stat().st_size >= min_sz:
                    if reject_gif and existing.suffix.lower() == ".gif":
                        try:
                            existing.unlink(missing_ok=True)  # type: ignore[call-arg]
                        except TypeError:
                            try:
                                if existing.is_file():
                                    existing.unlink()
                            except OSError:
                                pass
                        except OSError:
                            pass
                        continue
                    # Reject empty/broken / non-mugshot cached stubs
                    try:
                        head = existing.read_bytes()[:16]
                    except OSError:
                        continue
                    if head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n":
                        pass
                    elif head[:6] in (b"GIF87a", b"GIF89a") and reject_gif:
                        continue
                    elif len(head) < 8:
                        continue
                    try:
                        from scraper.mugshot_ethnicity.photo_quality import (
                            is_non_mugshot,
                        )

                        if is_non_mugshot(existing):
                            try:
                                existing.unlink()
                            except OSError:
                                pass
                            continue
                    except Exception:
                        pass
                    try:
                        return str(existing.relative_to(Path.cwd()))
                    except ValueError:
                        return str(existing)

            # Prefer a referer on the same registry host (SC/TN need this)
            parsed_photo = urlparse(base)
            host_referer = ""
            if parsed_photo.scheme and parsed_photo.netloc:
                host_referer = f"{parsed_photo.scheme}://{parsed_photo.netloc}/"
            referers: List[str] = []
            for r in (referer, host_referer, "https://www.nsopw.gov/"):
                r = (r or "").strip()
                if r and r not in referers:
                    referers.append(r)
            if not referers:
                referers = [""]

            for cand in photo_url_variants(base):
                for ref in referers:
                    path = self._download_photo_once(
                        cand,
                        photo_dir,
                        key=key,
                        referer=ref,
                        min_sz=min_sz,
                        reject_gif=reject_gif,
                    )
                    if path:
                        return path
            return None
        except Exception:
            return None

    def _download_photo_once(
        self,
        url: str,
        photo_dir: Path,
        *,
        key: str,
        referer: str,
        min_sz: int,
        reject_gif: bool,
    ) -> Optional[str]:
        """Single GET + validate + write. Returns local path or None."""
        headers: Dict[str, str] = {
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                headers.setdefault("Origin", f"{parsed.scheme}://{parsed.netloc}")

        resp = self._get_photo_response(url, headers=headers)
        if resp is None:
            return None
        if getattr(resp, "status_code", 0) >= 400:
            return None
        body = resp.content or b""
        if len(body) < min_sz:
            return None
        # Reject HTML error pages saved as images (SC often returns the portal HTML)
        head = body[:200].lstrip().lower()
        if head.startswith(b"<!doctype") or head.startswith(b"<html") or head.startswith(b"<?xml"):
            return None
        ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ct and not (
            ct.startswith("image/")
            or ct in ("application/octet-stream", "binary/octet-stream", "")
        ):
            if "json" in ct or "text/" in ct or "html" in ct:
                return None
        # Sniff magic (authoritative — SC labels PNG as Image/gif)
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            ext = ""
        if body[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif body[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif body[:6] in (b"GIF87a", b"GIF89a"):
            ext = ".gif"
        elif body[:4] == b"RIFF" and len(body) > 12 and body[8:12] == b"WEBP":
            ext = ".webp"
        elif not ext:
            guess = mimetypes.guess_extension(ct) or ""
            if guess == ".jpe":
                guess = ".jpg"
            ext = guess if guess in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp") else ".jpg"
        if reject_gif and ext == ".gif":
            return None
        # Reject 1×1 / seals / silhouettes / banner chrome before writing
        try:
            from scraper.mugshot_ethnicity.photo_quality import bytes_non_mugshot_reason

            bad = bytes_non_mugshot_reason(body, url=url, ext=ext)
            if bad:
                return None
        except Exception:
            pass
        # Content-Type image/gif with non-GIF magic is fine (already sniffed)
        dest = photo_dir / f"{key}{ext}"
        dest.write_bytes(body)
        # Double-check after write (dims/heuristics on file)
        try:
            from scraper.mugshot_ethnicity.photo_quality import is_non_mugshot

            if is_non_mugshot(dest):
                try:
                    dest.unlink()
                except OSError:
                    pass
                return None
        except Exception:
            pass
        try:
            return str(dest.relative_to(Path.cwd()))
        except ValueError:
            return str(dest)

    def _get_photo_response(self, url: str, *, headers: Dict[str, str]) -> Any:
        """GET image bytes; fall back to verify=False on TLS failures."""
        last_err: Optional[Exception] = None
        for verify in (True, False):
            try:
                return self.session.get(
                    url,
                    timeout=self.timeout,
                    headers=headers or None,
                    allow_redirects=True,
                    verify=verify,
                )
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                # Only retry without verify on SSL/cert problems
                if verify and (
                    "ssl" in msg
                    or "certificate" in msg
                    or "cert" in msg
                    or "curl: (60)" in msg
                    or "certificate_verify" in msg
                ):
                    continue
                if verify:
                    # Other errors: still try once without verify (some stacks
                    # wrap TLS failures poorly).
                    continue
                break
        # Last-ditch: stock requests (different CA store than curl_cffi)
        try:
            return requests.get(
                url,
                timeout=self.timeout,
                headers={**dict(getattr(self.session, "headers", {}) or {}), **headers},
                allow_redirects=True,
                verify=False,
            )
        except Exception:
            if last_err:
                raise last_err
            return None

    def _save_html(
        self,
        content: bytes,
        report_url: str,
        html_dir: Path,
        jurisdiction: str,
        final_url: str = "",
        download_images: bool = True,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Write report HTML to disk; rewrite <img> src to local copies when possible.

        Returns (html_path, primary_photo_path) — either may be None.
        """
        try:
            jur = re.sub(r"[^A-Za-z0-9_-]", "", (jurisdiction or "UNK").upper())[:12] or "UNK"
            digest = sha1((final_url or report_url).encode("utf-8", errors="replace")).hexdigest()[:16]
            folder = Path(html_dir) / jur
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"{digest}.html"
            assets = folder / f"{digest}_assets"
            base = final_url or report_url

            header = (
                f"<!-- archived_from: {html_lib.escape(final_url or report_url)} -->\n"
                f"<!-- archived_at_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} -->\n"
                f"<!-- photos_embedded: {str(bool(download_images)).lower()} -->\n"
            ).encode("utf-8")

            text = content.decode("utf-8", errors="replace")
            primary_photo: Optional[str] = None

            if download_images:
                text, primary_photo = self._embed_images_in_html(
                    text,
                    base_url=base,
                    assets_dir=assets,
                    assets_rel_name=f"{digest}_assets",
                    referer=base,
                )

            body_bytes = text.encode("utf-8", errors="replace")
            if not (dest.exists() and dest.stat().st_size > 100):
                dest.write_bytes(header + body_bytes)
            elif download_images:
                # Refresh archive so images are embedded even if HTML existed without them
                dest.write_bytes(header + body_bytes)

            try:
                html_path = str(dest.relative_to(Path.cwd()))
            except ValueError:
                html_path = str(dest)

            # Prefer photo already under assets next to HTML (never GIF chrome)
            if not primary_photo and assets.is_dir():
                ranked: List[Tuple[int, Path]] = []
                for p in assets.iterdir():
                    if not p.is_file():
                        continue
                    ext = p.suffix.lower()
                    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                        continue  # skip .gif site chrome
                    try:
                        sz = p.stat().st_size
                    except OSError:
                        continue
                    if sz < self.MIN_PHOTO_BYTES:
                        continue
                    score = sz + (50_000 if ext in (".jpg", ".jpeg") else 0)
                    ranked.append((score, p))
                if ranked:
                    ranked.sort(key=lambda t: t[0], reverse=True)
                    p = ranked[0][1]
                    try:
                        primary_photo = str(p.relative_to(Path.cwd()))
                    except ValueError:
                        primary_photo = str(p)

            return html_path, primary_photo
        except OSError:
            return None, None

    def _embed_images_in_html(
        self,
        html: str,
        *,
        base_url: str,
        assets_dir: Path,
        assets_rel_name: str,
        referer: str = "",
    ) -> Tuple[str, Optional[str]]:
        """
        Download remote images referenced by the report and rewrite HTML to local paths.
        Returns (modified_html, best_photo_path).
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return html, None

        assets_dir.mkdir(parents=True, exist_ok=True)
        url_to_local: Dict[str, str] = {}
        primary: Optional[str] = None
        candidates: List[Tuple[int, str, str]] = []  # score, abs_url, local_path

        def _abs(src: str) -> str:
            s = (src or "").strip()
            if not s or s.startswith("data:") or s.startswith("javascript:"):
                return ""
            return urljoin(base_url, s)

        def _score_url(u: str, el_tag: str = "img") -> int:
            low = u.lower()
            score = 10
            for bad in (
                "logo", "icon", "sprite", "pixel", "tracking", "1x1", "spacer",
                "banner", "button", "header", "footer", "seal", "badge", "map",
                "help", "question", "chat", "tooltip",
            ):
                if bad in low:
                    score -= 8
            for good in (
                "photo", "offender", "mug", "portrait", "image", "pic", "face",
                "sor", "reg", "callimage", "imgid", "displayimage", "pictures/",
            ):
                if good in low:
                    score += 8
            # Dedicated mugshot endpoints / CDNs beat decorative chrome
            if (
                "callimage" in low
                or "imgid=" in low
                or "/sorimage/" in low
                or "displayimage" in low
            ):
                score += 20
            # AL iCrimewatch: real mugshots live under /pictures/; office headers under /offices/
            if "watchsystems.com" in low and "/pictures/" in low:
                score += 35
            if "/pictures/" in low:
                score += 15
            if "/offices/" in low:
                score -= 40
            if el_tag == "img":
                score += 2
            if any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                score += 5
            if low.endswith(".gif") or ".gif?" in low:
                score -= 25
            return score

        def _aspect_score(local_path: str) -> int:
            """Prefer portrait/square mugshots; penalize wide banners and tiny icons."""
            try:
                from PIL import Image

                with Image.open(local_path) as im:
                    w, h = im.size
                if w < 1 or h < 1:
                    return -15
                if min(w, h) < 40:
                    return -25
                ratio = max(w, h) / float(min(w, h))
                # Sheriff office banners are often ~800x200 (ratio 4)
                if ratio >= 2.4:
                    return -30
                # Typical mugshot / headshot
                ar = w / float(h)
                if 0.55 <= ar <= 1.35:
                    return 12
                return 0
            except Exception:
                return 0

        # Collect img src (+ srcset first url) and meta og:image
        img_srcs: List[Tuple[Any, str]] = []
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            if not src and img.get("srcset"):
                src = (img.get("srcset") or "").split(",")[0].strip().split(" ")[0]
            if src:
                img_srcs.append((img, src))

        for meta in soup.find_all("meta"):
            prop = (meta.get("property") or meta.get("name") or "").lower()
            if prop in ("og:image", "twitter:image", "twitter:image:src"):
                content = meta.get("content") or ""
                if content:
                    img_srcs.append((meta, content))

        for el, src in img_srcs:
            abs_u = _abs(src)
            if not abs_u:
                continue
            # Skip seals / spacers / tracking before network I/O
            try:
                from scraper.mugshot_ethnicity.photo_quality import url_looks_like_chrome

                if url_looks_like_chrome(abs_u):
                    continue
            except Exception:
                pass
            # Alt text: skip explicit chrome labels
            try:
                alt0 = (el.get("alt") or "").strip().lower() if hasattr(el, "get") else ""
            except Exception:
                alt0 = ""
            if alt0 and any(
                k in alt0
                for k in (
                    "seal",
                    "logo",
                    "spacer",
                    "skip navigation",
                    "banner",
                    "icon",
                    "sheriff's office",
                    "sheriffs office",
                )
            ):
                # Keep real "offender photo" etc.
                if not any(k in alt0 for k in ("offender", "mug", "registrant", "photo of")):
                    continue
            if abs_u in url_to_local:
                local = url_to_local[abs_u]
            else:
                stem = sha1(abs_u.encode("utf-8", errors="replace")).hexdigest()[:14]
                local = self.download_photo(
                    abs_u, assets_dir, referer=referer or base_url, stem=stem
                )
                if not local:
                    continue
                url_to_local[abs_u] = local
                score = _score_url(abs_u, getattr(el, "name", "img") or "img")
                # Alt text: icrimewatch sets alt='Offender photo' on the mugshot
                try:
                    alt = (el.get("alt") or "").strip().lower() if hasattr(el, "get") else ""
                except Exception:
                    alt = ""
                if alt:
                    if any(k in alt for k in ("offender", "mug", "photo of", "registrant")):
                        score += 30
                    elif "photo" in alt and "office" not in alt:
                        score += 18
                    if any(k in alt for k in ("sheriff", "office", "search", "email", "tip", "logo")):
                        score -= 25
                try:
                    fsz = Path(local).stat().st_size
                    fext = Path(local).suffix.lower()
                except OSError:
                    fsz = 0
                    fext = ""
                # Size boost: real mugshots are usually multi-KB; shared site
                # chrome (icons/badges) is often 1–2KB and repeats across records.
                # Large GIFs (FL banners ~30KB) must still lose to JPEG CallImage.
                if fext == ".gif":
                    score -= 30
                if fext in (".jpg", ".jpeg", ".png", ".webp"):
                    score += 10
                if fsz >= self.MIN_PRIMARY_PHOTO_BYTES:
                    score += 8
                elif fsz >= 800:
                    score += 2
                else:
                    score -= 10
                # Mild size preference — but aspect ratio matters more than raw KB
                # (AL office banners are often larger files than mugshots).
                score += min(fsz // 20000, 3)
                score += _aspect_score(local)
                candidates.append((score, fsz, abs_u, local))

            # Rewrite to relative path next to the HTML file
            local_name = Path(local).name
            rel = f"{assets_rel_name}/{local_name}"
            if getattr(el, "name", "") == "img":
                el["src"] = rel
                if el.get("data-src"):
                    el["data-src"] = rel
                if el.get("srcset"):
                    el["srcset"] = rel
            elif getattr(el, "name", "") == "meta":
                el["content"] = rel

        if candidates:
            # Prefer high score, then larger file; never pick non-mugshot chrome
            try:
                from scraper.mugshot_ethnicity.photo_quality import is_non_mugshot
            except Exception:
                is_non_mugshot = lambda _p: False  # type: ignore
            candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
            for best in candidates:
                local_p = best[3]
                try:
                    if is_non_mugshot(local_p):
                        continue
                except Exception:
                    pass
                # Only treat as primary mugshot if large enough; otherwise leave
                # photo_path for _ensure_photo to fill from NSOPW imageUri.
                if best[1] >= self.MIN_PRIMARY_PHOTO_BYTES or best[0] >= 20:
                    primary = local_p
                    break
                if best[1] >= 500:
                    primary = local_p
                    break

        try:
            out = str(soup)
        except Exception:
            out = html
        return out, primary

    def _from_html(self, html: str, base_url: str = "") -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        found: Dict[str, Any] = {}

        # Keep a raw copy for regex patterns before tag stripping
        raw_html = html

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        # --- Header-row tables (OK Apex, many report grids) ---
        # Require multiple <th> headers (not label|value rows that use th/td pairs).
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            ths = rows[0].find_all("th")
            if len(ths) < 2:
                continue
            headers = [_normalize_label(c.get_text(" ", strip=True)) for c in ths]
            mapped_n = sum(1 for h in headers if h in _LABEL_MAP)
            if mapped_n < 2:
                continue
            for data_row in rows[1:]:
                tds = data_row.find_all("td")
                if not tds:
                    continue
                values = [_clean_value(td.get_text(" ", strip=True)) for td in tds]
                crime_parts = []
                for h, v in zip(headers, values):
                    key = _LABEL_MAP.get(h)
                    if not key or not v or _normalize_label(v) in _LABEL_MAP:
                        continue
                    if key == "crime":
                        crime_parts.append(v)
                    else:
                        found.setdefault(key, v)
                if crime_parts:
                    joined = " — ".join(crime_parts)[:_MAX_CRIME_LEN]
                    prev = found.get("crime") or ""
                    if not prev:
                        found["crime"] = joined
                    elif joined not in prev:
                        found["crime"] = f"{prev}; {joined}"[:_MAX_CRIME_LEN]
                if any(k in found for k in ("race", "gender", "height", "crime")):
                    break

        # PrimeFaces / FL style: alternating label/value cells
        cells = soup.select("div.borderPanelCell, div.ui-g-12.borderPanelCell")
        if len(cells) >= 2:
            i = 0
            while i < len(cells) - 1:
                lab = _normalize_label(cells[i].get_text(" ", strip=True))
                val = _clean_value(cells[i + 1].get_text(" ", strip=True))
                if lab in _LABEL_MAP and val and len(val) < 120 and lab != _normalize_label(val):
                    found.setdefault(_LABEL_MAP[lab], val)
                    i += 2
                else:
                    i += 1

        # Bootstrap / CA Megans Law: Label:</div><div class="col-…">VALUE</div>
        for m in re.finditer(
            r"(Race|Ethnicity|Sex|Gender|Height|Weight|Hair Color|Eye Color|Eyes|Hair|"
            r"Date of Birth|DOB|Age)\s*:?\s*</(?:div|span|label|strong|b|dt|th)>\s*"
            r"<(?:div|span|dd|td)[^>]*>\s*([^<]{1,80}?)\s*</(?:div|span|dd|td)>",
            raw_html,
            flags=re.I,
        ):
            key = _LABEL_MAP.get(m.group(1).lower())
            if key:
                found.setdefault(key, _clean_value(m.group(2)))

        # DE / label + sibling span (possibly empty if Knockout not rendered)
        for lab in soup.find_all("label"):
            label = _normalize_label(lab.get_text(" ", strip=True))
            if label not in _LABEL_MAP:
                continue
            val = ""
            fid = lab.get("for")
            if fid:
                target = soup.find(id=fid)
                if target is not None:
                    val = _clean_value(target.get_text(" ", strip=True))
            if not val:
                for sib in lab.find_next_siblings(["span", "div", "p", "td"]):
                    val = _clean_value(sib.get_text(" ", strip=True))
                    if val and _normalize_label(val) not in _LABEL_MAP:
                        break
                    val = ""
            if not val and lab.parent is not None:
                # parent row: "Race: | Black"
                ptext = lab.parent.get_text(" ", strip=True)
                rest = re.sub(
                    re.escape(lab.get_text(" ", strip=True)),
                    "",
                    ptext,
                    count=1,
                    flags=re.I,
                ).strip(" :-|")
                if rest and len(rest) < 80:
                    val = _clean_value(rest)
            if val and val not in ("(unknown)", "—", "-") and not val.startswith("("):
                found.setdefault(_LABEL_MAP[label], val)

        for dt in soup.find_all("dt"):
            label = _normalize_label(dt.get_text(" ", strip=True))
            dd = dt.find_next_sibling("dd")
            if dd and label in _LABEL_MAP:
                found.setdefault(_LABEL_MAP[label], _clean_value(dd.get_text(" ", strip=True)))

        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"])
            # Pair adjacent cells: [label, value, label, value, ...]
            # (iCrimeWatch rows often pack two fields per <tr>)
            i = 0
            while i < len(cells) - 1:
                label = _normalize_label(cells[i].get_text(" ", strip=True))
                value = _clean_value(cells[i + 1].get_text(" ", strip=True))
                key = _LABEL_MAP.get(label)
                max_len = _MAX_CRIME_LEN if key in _LONG_VALUE_KEYS else 200
                if (
                    key
                    and value
                    and len(value) <= max_len
                    and _normalize_label(value) not in _LABEL_MAP
                ):
                    found.setdefault(key, value[:max_len])
                    i += 2
                else:
                    i += 1

        # Offense / crime tables (column headers like Offense, Charge, Statute)
        crime_bits = self._extract_crime_from_tables(soup)
        if crime_bits:
            prev = (found.get("crime") or "").strip()
            if not prev:
                found["crime"] = crime_bits
            elif len(crime_bits) > len(prev):
                found["crime"] = crime_bits
            elif crime_bits not in prev:
                found["crime"] = f"{prev}; {crime_bits}"[:_MAX_CRIME_LEN]

        # Label-only node → next sibling element holds value (common grid layouts)
        for lab_el in soup.find_all(["span", "div", "label", "strong", "b", "th", "td", "p"]):
            raw = lab_el.get_text(" ", strip=True)
            if not raw or len(raw) > 60:
                continue
            label = _normalize_label(raw)
            if label not in _LABEL_MAP:
                # "Race: White" on same node
                m = re.match(
                    r"^(Race|Ethnicity|Sex|Gender|Height|Weight|Eye Color|Hair Color|"
                    r"Eyes|Hair|Age|DOB|Date of Birth)\s*[:\-]\s*(.+)$",
                    raw,
                    flags=re.I,
                )
                if m:
                    key = _LABEL_MAP.get(m.group(1).lower())
                    if key:
                        found.setdefault(key, m.group(2).strip()[:120])
                continue
            # empty value on label node → look next
            if ":" in raw and not re.search(r":\s*\S", raw):
                # parent then next sibling
                parent = lab_el.parent
                candidates = []
                if parent is not None:
                    candidates.append(parent.find_next_sibling())
                candidates.append(lab_el.find_next_sibling())
                for nxt in candidates:
                    if not nxt or not hasattr(nxt, "get_text"):
                        continue
                    val = nxt.get_text(" ", strip=True)
                    if val and len(val) < 80 and _normalize_label(val) not in _LABEL_MAP:
                        found.setdefault(_LABEL_MAP[label], val)
                        break

        for lab in soup.find_all(["label", "strong", "b", "span", "div", "p"]):
            raw = lab.get_text(" ", strip=True)
            if not raw or len(raw) > 80:
                continue
            m = re.match(
                r"^(Race|Ethnicity|Sex|Gender|Height|Weight|Eye Color|Hair Color|Age|DOB|Date of Birth)\s*[:\-]\s*(.+)$",
                raw,
                flags=re.I,
            )
            if m:
                key = _LABEL_MAP.get(m.group(1).lower())
                if key:
                    found.setdefault(key, m.group(2).strip())
                continue

            label = _normalize_label(raw)
            if label in _LABEL_MAP:
                nxt = lab.find_next_sibling(string=True)
                if nxt and str(nxt).strip():
                    found.setdefault(_LABEL_MAP[label], str(nxt).strip())
                    continue
                parent = lab.parent
                if parent:
                    ptext = parent.get_text(" ", strip=True)
                    rest = re.sub(re.escape(raw), "", ptext, count=1, flags=re.I).strip(" :-")
                    if rest and len(rest) < 80:
                        found.setdefault(_LABEL_MAP[label], rest)

        body_text = soup.get_text("\n", strip=True)
        for line in body_text.splitlines():
            m = re.match(
                r"^(Race|Ethnicity|Sex|Gender|Height|Weight|Eye Color|Hair Color|Age|"
                r"Date of Birth|DOB|Offense|Offense Description|Charge|Charges|Crime|"
                r"Qualifying Offense|Registerable Offense|Statute)\s*[:\-]\s*(.+)$",
                line.strip(),
                flags=re.I,
            )
            if m:
                key = _LABEL_MAP.get(_normalize_label(m.group(1)))
                if key:
                    lim = _MAX_CRIME_LEN if key in _LONG_VALUE_KEYS else 120
                    found.setdefault(key, m.group(2).strip()[:lim])

        # Re-parse scripts from original HTML for embedded JSON
        for script in BeautifulSoup(html, "html.parser").find_all("script"):
            content = script.string or ""
            if len(content) >= 500_000:
                continue
            low = content.lower()
            if "race" in low or "offense" in low or "charge" in low:
                for m in re.finditer(
                    r'"(race|ethnicity|gender|sex|height|weight|eyeColor|hairColor|'
                    r'offense|offenseDescription|offenseType|charge|charges|crime|'
                    r'statute|qualifyingOffense)"\s*:\s*"([^"]{1,400})"',
                    content,
                    flags=re.I,
                ):
                    raw_key = m.group(1).lower()
                    key = {
                        "race": "race",
                        "ethnicity": "ethnicity",
                        "gender": "gender",
                        "sex": "gender",
                        "height": "height",
                        "weight": "weight",
                        "eyecolor": "eye_color",
                        "haircolor": "hair_color",
                        "offense": "crime",
                        "offensedescription": "crime",
                        "offensetype": "offense_type",
                        "charge": "crime",
                        "charges": "crime",
                        "crime": "crime",
                        "statute": "crime",
                        "qualifyingoffense": "crime",
                    }.get(raw_key)
                    if key:
                        found.setdefault(key, m.group(2)[:_MAX_CRIME_LEN])

        if "age" in found:
            try:
                found["age"] = int(re.sub(r"[^\d]", "", str(found["age"])) or 0) or found["age"]
            except (TypeError, ValueError):
                pass

        # Drop bogus gender values
        g = str(found.get("gender") or "").strip().lower()
        if g in ("minor", "description", "status", "yes", "no"):
            found.pop("gender", None)

        # Clean all string fields
        for k, v in list(found.items()):
            if isinstance(v, str):
                found[k] = _clean_value(v)

        # CA and others often use Ethnicity where Race is expected
        if not found.get("race") and found.get("ethnicity"):
            eth = str(found["ethnicity"]).strip()
            if eth and eth.lower() not in ("unknown", "undetermined", "n/a", "none"):
                found["race"] = eth

        # Synthesize primary crime field from any offense pieces
        self._finalize_crime_fields(found)

        if base_url:
            found["report_final_url"] = base_url
        return found

    @staticmethod
    def _extract_crime_from_tables(soup: BeautifulSoup) -> str:
        """Pull offense/charge text from multi-row offense tables."""
        crime_header_keys = {
            "offense", "offenses", "offense description", "offense type",
            "charge", "charges", "crime", "crimes", "statute",
            "qualifying offense", "registerable offense", "registrable offense",
            "description", "violation",
        }
        collected: List[str] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [
                _normalize_label(c.get_text(" ", strip=True))
                for c in rows[0].find_all(["th", "td"])
            ]
            # Find offense-like columns
            idxs = [i for i, h in enumerate(headers) if h in crime_header_keys]
            if not idxs:
                # Header might be a caption / first cell "Offense Information"
                head_blob = " ".join(headers).lower()
                if not any(k in head_blob for k in ("offense", "charge", "crime", "statute")):
                    continue
                # Use all non-empty data cells as crime text
                for data_row in rows[1:]:
                    tds = data_row.find_all("td")
                    for td in tds:
                        t = _clean_value(td.get_text(" ", strip=True))
                        if t and len(t) > 3 and _normalize_label(t) not in _LABEL_MAP:
                            collected.append(t)
                continue
            for data_row in rows[1:]:
                tds = data_row.find_all(["td", "th"])
                parts = []
                for i in idxs:
                    if i < len(tds):
                        t = _clean_value(tds[i].get_text(" ", strip=True))
                        if t and _normalize_label(t) not in crime_header_keys:
                            parts.append(t)
                if parts:
                    collected.append(" — ".join(parts))
        # Deduplicate preserving order
        seen = set()
        uniq: List[str] = []
        for c in collected:
            key = c.lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(c)
            if len(uniq) >= 8:
                break
        return "; ".join(uniq)[:_MAX_CRIME_LEN]

    @staticmethod
    def _finalize_crime_fields(found: Dict[str, Any]) -> None:
        """Ensure `crime` is set for display; keep offense_* in sync when possible."""
        crime = (found.get("crime") or "").strip()
        otype = (found.get("offense_type") or "").strip()
        odesc = (found.get("offense_description") or "").strip()
        if not crime:
            if odesc and otype and odesc.lower() != otype.lower():
                crime = f"{otype}: {odesc}"
            else:
                crime = odesc or otype
        if crime:
            found["crime"] = crime[:_MAX_CRIME_LEN]
            if not odesc:
                found["offense_description"] = crime[:_MAX_CRIME_LEN]
            if not otype and len(crime) < 120:
                found["offense_type"] = crime

    def _from_json_blob(self, data: Any, prefix: str = "") -> Dict[str, Any]:
        found: Dict[str, Any] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                kl = str(k).lower().replace("_", "")
                mapped = {
                    "race": "race",
                    "ethnicity": "ethnicity",
                    "gender": "gender",
                    "sex": "gender",
                    "height": "height",
                    "weight": "weight",
                    "offense": "crime",
                    "offensedescription": "crime",
                    "offensetype": "offense_type",
                    "charge": "crime",
                    "charges": "crime",
                    "crime": "crime",
                    "statute": "crime",
                    "qualifyingoffense": "crime",
                    "eyecolor": "eye_color",
                    "haircolor": "hair_color",
                    "skintone": "skin_tone",
                    "build": "build",
                    "age": "age",
                    "dateofbirth": "date_of_birth",
                    "dob": "date_of_birth",
                    "county": "county",
                    "city": "city",
                    "address": "address",
                    "risklevel": "risk_level",
                }.get(kl)
                if mapped and isinstance(v, (str, int, float)) and str(v).strip():
                    found.setdefault(mapped, v)
                elif isinstance(v, (dict, list)) and len(str(v)) < 10000:
                    found.update(self._from_json_blob(v))
        elif isinstance(data, list):
            for item in data[:50]:
                found.update(self._from_json_blob(item))
        return found

    def _try_texas_json(self, report_url: str) -> Optional[Dict[str, Any]]:
        if "sor.dps.texas.gov" not in report_url.lower():
            return None
        m = re.search(r"[?&]sid=([^&]+)", report_url, flags=re.I)
        if not m:
            return None
        sid = m.group(1)
        candidates = [
            f"https://sor.dps.texas.gov/Search/Rapsheet/Index?sid={sid}&handler=GetRapsheet",
            f"https://publicsite.dps.texas.gov/SexOffenderRegistry/Search/Rapsheet?sid={sid}",
        ]
        for url in candidates:
            try:
                resp = self._get(
                    url,
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": report_url,
                    },
                )
                if resp.status_code != 200:
                    continue
                ct = (resp.headers.get("Content-Type") or "").lower()
                if "json" in ct:
                    return self._from_json_blob(resp.json())
                # Sometimes returns JSON with wrong content-type
                text = resp.text.strip()
                if text.startswith("{") or text.startswith("["):
                    try:
                        import json as _json

                        return self._from_json_blob(_json.loads(text))
                    except ValueError:
                        pass
                if "race" in resp.text.lower():
                    return self._from_html(resp.text, base_url=resp.url)
            except Exception:
                continue
        return None
