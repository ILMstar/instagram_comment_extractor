"""Instagrapi-based comment provider (Mobile/Private API).

Uses Instagram's Private (Mobile) API via instagrapi, which is significantly
more reliable than the web GraphQL endpoints that instaloader depends on.
Instagram has progressively blocked the GraphQL comment query_hash endpoints,
making instaloader's comment pagination unreliable since late 2024.

This provider can import session cookies from instaloader's saved sessions,
so users don't need to re-authenticate.
"""

import logging
import pickle
from pathlib import Path
import time
from typing import Any, Callable, Generator, Iterable

from src.extractor import CommentProvider, PostMetadata, RateLimitExceededError, RawComment

logger = logging.getLogger(__name__)

DEFAULT_SESSION_DIR = Path.home() / ".instagrapi_sessions"

# Substrings in exception messages that indicate a retryable rate-limit error
_RETRYABLE_KEYWORDS = ("please wait", "rate limit", "throttled", "try again", "few minutes")


def _extract_sessionid_from_instaloader(username: str, session_file: Path | None = None) -> str | None:
    """Extracts the sessionid cookie from an instaloader pickle session file.

    Args:
        username: Instagram username (used to locate default session file).
        session_file: Optional explicit path to the instaloader session file.

    Returns:
        The sessionid string, or None if extraction fails or cookie is empty.
    """
    if session_file and session_file.exists():
        target = session_file
    else:
        try:
            import instaloader.instaloader as il_mod
            default_path = Path(il_mod.get_default_session_filename(username))
            if default_path.exists():
                target = default_path
            else:
                target = None
        except Exception:
            target = None

        if target is None:
            candidates = [
                Path.home() / "AppData" / "Local" / "Instaloader" / f"session-{username}",
                Path.home() / ".config" / "instaloader" / f"session-{username}",
            ]
            target = next((p for p in candidates if p.exists()), None)
            if target is None:
                return None

    try:
        with open(target, "rb") as f:
            cookie_jar = pickle.load(f)  # noqa: S301 - trusted local file

        # Instaloader saves session as a dict: {cookie_name: cookie_val}
        if isinstance(cookie_jar, dict):
            sid = cookie_jar.get("sessionid")
            if sid and str(sid).strip():
                logger.info("Extracted sessionid from instaloader session dict at %s", target)
                return str(sid).strip()
            logger.warning("Instaloader session at %s contains an empty or missing sessionid", target)
            return None

        # Fallback if cookie_jar is an iterable of Cookie objects
        cookie_list = getattr(cookie_jar, "cookies", cookie_jar)
        for cookie in cookie_list:
            if getattr(cookie, "name", None) == "sessionid":
                val = getattr(cookie, "value", None)
                if val and str(val).strip():
                    logger.info("Extracted sessionid from instaloader session at %s", target)
                    return str(val).strip()

        logger.warning("No sessionid cookie found in %s", target)
        return None
    except Exception as exc:
        logger.warning("Failed to read instaloader session file %s: %s", target, exc)
        return None


class InstagrapiCommentProvider(CommentProvider):
    """Comment provider using instagrapi's Private/Mobile API.

    This provider is more reliable than the GraphQL-based instaloader provider
    because it uses the same API endpoints as the official Instagram mobile app.

    Login strategy (tried in order):
      1. Direct sessionid provided (fastest & bypasses 2FA/checkpoints).
      2. Load a saved instagrapi session JSON file.
      3. Import the sessionid cookie from an existing instaloader session.
      4. Fresh login with username + password.
    """

    def __init__(
        self,
        session_user: str | None = None,
        session_id: str | None = None,
        password: str | None = None,
        session_file: Path | None = None,
        instaloader_session_file: Path | None = None,
        max_retries: int = 5,
        base_delay_sec: float = 5.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        from instagrapi import Client

        self.max_retries = max_retries
        self.base_delay_sec = base_delay_sec
        self.sleep_fn = sleep_fn

        self.client = Client()
        self.client.delay_range = [2, 5]  # random delay between private API requests

        # Determine where to save/load the instagrapi session
        if session_file:
            self._session_path = Path(session_file)
        elif session_user:
            self._session_path = DEFAULT_SESSION_DIR / f"{session_user}.json"
        else:
            self._session_path = DEFAULT_SESSION_DIR / "default.json"

        self._login(session_user, password, session_id, instaloader_session_file)

    def _login(
        self,
        username: str | None,
        password: str | None,
        session_id: str | None,
        instaloader_session_file: Path | None,
    ) -> None:
        """Handles login with automatic session import from instaloader."""
        # Strategy 1: Direct sessionid provided (most reliable & bypasses 2FA/checkpoints)
        if session_id and str(session_id).strip():
            clean_sid = str(session_id).strip()
            try:
                logged_in = self.client.login_by_sessionid(clean_sid)
                if logged_in:
                    try:
                        info = self.client.account_info()
                        if info and info.username and not username:
                            username = info.username
                            self._session_path = DEFAULT_SESSION_DIR / f"{username}.json"
                    except Exception:
                        pass
                    self._save_session()
                    logger.info("Authenticated successfully using provided sessionid")
                    return
            except Exception as exc:
                raise RateLimitExceededError(
                    f"Provided sessionid was rejected by Instagram: {exc}. "
                    "Make sure you copied the full active sessionid cookie from a logged-in browser session."
                ) from exc

        # Strategy 2: Load saved instagrapi session
        if self._session_path and self._session_path.exists():
            try:
                self.client.load_settings(self._session_path)
                self.client.login(username or "", "")
                # Validate the session is still alive
                self.client.get_timeline_feed()
                logger.info("Loaded valid instagrapi session for %s", username)
                return
            except Exception as exc:
                logger.warning(
                    "Saved instagrapi session invalid (%s), trying other methods...", exc
                )
                from instagrapi import Client
                self.client = Client()
                self.client.delay_range = [2, 5]

        # Strategy 3: Import sessionid from instaloader
        if username and not password:
            extracted_sid = _extract_sessionid_from_instaloader(
                username, session_file=instaloader_session_file
            )
            if extracted_sid:
                try:
                    logged_in = self.client.login_by_sessionid(extracted_sid)
                    if logged_in:
                        self._save_session()
                        logger.info(
                            "Imported instaloader session for %s into instagrapi", username
                        )
                        return
                except Exception as exc:
                    logger.warning("Imported sessionid rejected by mobile API: %s", exc)
                    from instagrapi import Client
                    self.client = Client()
                    self.client.delay_range = [2, 5]
            else:
                logger.info(
                    "No valid sessionid in instaloader session file for %s", username
                )

        # Strategy 4: Fresh login with credentials
        if username and password:
            try:
                self.client.login(username, password)
                self._save_session()
                logger.info("Fresh login successful for %s via instagrapi", username)
                return
            except Exception as exc:
                raise RateLimitExceededError(
                    f"Login failed for {username}: {exc}. "
                    f"Check credentials or try pasting your browser's sessionid cookie."
                ) from exc

        # No strategy worked
        raise RateLimitExceededError(
            "No active Instagram session available. "
            "Your saved session file contains an empty or expired sessionid. "
            "Please provide your 'sessionid' cookie from your browser, or enter your Instagram password under Session Authentication."
        )

    def _save_session(self) -> None:
        """Persists instagrapi session to disk for future reuse."""
        if self._session_path:
            self._session_path.parent.mkdir(parents=True, exist_ok=True)
            self.client.dump_settings(self._session_path)
            logger.info("Saved instagrapi session to %s", self._session_path)

    def _is_retryable(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(kw in msg for kw in _RETRYABLE_KEYWORDS)

    def _retry(self, fn: Callable[[], Any], description: str = "operation") -> Any:
        """Retries a callable with exponential backoff on rate-limit errors."""
        for attempt in range(self.max_retries + 1):
            try:
                return fn()
            except Exception as exc:
                if not self._is_retryable(exc) or attempt >= self.max_retries:
                    if self._is_retryable(exc):
                        raise RateLimitExceededError(
                            f"{description} failed after {self.max_retries} retries: {exc}"
                        ) from exc
                    raise
                delay = self.base_delay_sec * (2 ** attempt)
                logger.info(
                    "Retry %d/%d for %s in %.1fs: %s",
                    attempt + 1, self.max_retries, description, delay, exc,
                )
                self.sleep_fn(delay)

    def fetch_post(self, shortcode: str) -> PostMetadata:
        """Fetches post metadata and creates a lazy comment stream via mobile API."""
        # Convert shortcode → media_pk (local base64 decode, no network)
        media_pk = self.client.media_pk_from_code(shortcode)
        media_id = str(media_pk)

        # Fetch post info for the owner username
        media_info = self._retry(
            lambda: self.client.media_info(media_pk),
            description=f"Fetching media info for {shortcode}",
        )
        owner_username = media_info.user.username if media_info.user else ""

        def comment_generator() -> Generator[RawComment, None, None]:
            params: dict[str, Any] = {"can_support_threading": "true"}

            while True:
                res = self._retry(
                    lambda: self.client.private_request(f"media/{media_id}/comments/", params),
                    description=f"Fetching comments for {shortcode}",
                )
                raw_comments = res.get("comments", [])
                if not raw_comments:
                    break

                for c in raw_comments:
                    username = c.get("user", {}).get("username", "unknown")
                    c_pk = c.get("pk")
                    child_count = c.get("child_comment_count", 0) or 0
                    preview_replies = c.get("preview_child_comments", []) or []

                    def make_reply_gen(parent_pk: Any, count: int, preloaded: list) -> Iterable[RawComment]:
                        """Creates a lazy reply generator for threaded child comments."""
                        def gen() -> Generator[RawComment, None, None]:
                            seen_pks = set()
                            # 1. Yield any preloaded preview replies first
                            for r in preloaded:
                                r_pk = r.get("pk")
                                if r_pk:
                                    seen_pks.add(r_pk)
                                r_user = r.get("user", {}).get("username", "unknown")
                                yield RawComment(owner_username=r_user, replies=[])

                            # 2. If there are additional replies, fetch full thread
                            if count > len(preloaded) and parent_pk:
                                try:
                                    full_replies = self._retry(
                                        lambda: self.client.media_comment_replies(
                                            media_id, str(parent_pk), amount=0
                                        ),
                                        description=f"Fetching replies for comment {parent_pk}",
                                    )
                                    for r in full_replies:
                                        if r.pk not in seen_pks:
                                            seen_pks.add(r.pk)
                                            r_user = r.user.username if r.user else "unknown"
                                            yield RawComment(owner_username=r_user, replies=[])
                                except Exception as exc:
                                    logger.warning(
                                        "Failed to fetch replies for comment %s: %s", parent_pk, exc
                                    )
                        return gen()

                    yield RawComment(
                        owner_username=username,
                        replies=make_reply_gen(c_pk, child_count, preview_replies),
                    )

                # Check pagination cursor
                has_next = (res.get("has_more_comments") and res.get("next_max_id")) or (
                    res.get("has_more_headload_comments") and res.get("next_min_id")
                )
                if not has_next:
                    break

                params = {"can_support_threading": "true"}
                if res.get("has_more_comments") and res.get("next_max_id"):
                    params["max_id"] = res.get("next_max_id")
                elif res.get("has_more_headload_comments") and res.get("next_min_id"):
                    params["min_id"] = res.get("next_min_id")

        return PostMetadata(
            shortcode=shortcode,
            owner_username=owner_username,
            comments=comment_generator(),
        )

