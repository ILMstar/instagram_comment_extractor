"""Instagram URL parser module.

Validates Instagram post, reel, and TV URLs, strips tracking/query parameters,
rejects unsupported links (e.g., stories, profile pages), and extracts the
canonical shortcode.
"""

from dataclasses import dataclass
import re
from urllib.parse import urlparse

ALLOWED_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
}

PATH_PATTERN = re.compile(r"^/(p|reel|tv)/([A-Za-z0-9_-]+)/?$")


class InvalidInstagramURLError(ValueError):
    """Raised when a URL is not a valid Instagram Post, Reel, or TV link."""


@dataclass(frozen=True)
class ParsedPostURL:
    shortcode: str
    canonical_url: str
    media_type: str  # "p" | "reel" | "tv"


def parse_instagram_url(raw_url: str) -> ParsedPostURL:
    """Extracts shortcode and media type or raises InvalidInstagramURLError."""
    clean_url = raw_url.strip()
    if not clean_url:
        raise InvalidInstagramURLError("URL cannot be empty.")

    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    try:
        parsed = urlparse(clean_url)
    except Exception as exc:
        raise InvalidInstagramURLError(f"Failed to parse URL '{raw_url}': {exc}") from exc

    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_HOSTS:
        raise InvalidInstagramURLError(
            f"Host '{hostname}' is not a valid Instagram host. Expected one of {sorted(ALLOWED_HOSTS)}."
        )

    match = PATH_PATTERN.match(parsed.path)
    if not match:
        raise InvalidInstagramURLError(
            f"Path '{parsed.path}' is not a valid Instagram post, reel, or TV link."
        )

    media_type = match.group(1)
    shortcode = match.group(2)
    canonical_url = f"https://www.instagram.com/{media_type}/{shortcode}/"

    return ParsedPostURL(
        shortcode=shortcode,
        canonical_url=canonical_url,
        media_type=media_type,
    )

