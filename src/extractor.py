"""Core comment extraction engine.

Orchestrates comment traversal, duplicates preservation, and rate-limit handling.
"""

from dataclasses import dataclass
from typing import Iterable, List, Protocol


@dataclass(frozen=True)
class RawComment:
    owner_username: str
    replies: Iterable["RawComment"]


@dataclass(frozen=True)
class PostMetadata:
    shortcode: str
    owner_username: str
    comments: Iterable[RawComment]


class RateLimitExceededError(RuntimeError):
    """Raised by a provider when Instagram blocks further pagination."""


class CommentProvider(Protocol):
    def fetch_post(self, shortcode: str) -> PostMetadata:
        ...


@dataclass(frozen=True)
class ExtractionResult:
    shortcode: str
    usernames: List[str]
    is_partial: bool
    warning_message: str | None = None


def extract_usernames(
    shortcode: str,
    provider: CommentProvider,
    include_owner: bool = False,
    include_replies: bool = True,
) -> ExtractionResult:
    """Traverses comments, preserves duplicates, and recovers partial data on RateLimitExceededError."""
    usernames: List[str] = []
    is_partial = False
    warning_message: str | None = None

    try:
        post = provider.fetch_post(shortcode)
        owner = post.owner_username

        def process_comment(comment: RawComment) -> None:
            if include_owner or comment.owner_username != owner:
                usernames.append(comment.owner_username)
            if include_replies and comment.replies:
                for reply in comment.replies:
                    process_comment(reply)

        for comment in (post.comments or []):
            process_comment(comment)

    except RateLimitExceededError as exc:
        is_partial = True
        warning_message = f"Rate limit exceeded during extraction: {exc}"

    return ExtractionResult(
        shortcode=shortcode,
        usernames=usernames,
        is_partial=is_partial,
        warning_message=warning_message,
    )

