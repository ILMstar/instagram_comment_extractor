"""Slice 2 Tests: Nested Replies, Post Owner Exclusion & Partial Rate-Limit Recovery.

Covers:
- Nested replies extraction immediately after parent comment in exact order
- Depth-first traversal for multi-level replies
- Exclusion of post.owner_username by default (include_owner=False)
- Inclusion of post.owner_username when include_owner=True
- Disabling reply traversal when include_replies=False
- Graceful recovery when RateLimitExceededError occurs mid-stream
- CLI arguments (--include-owner, --no-replies) and stderr warning on partial recovery
"""

from pathlib import Path
from typing import Generator
import pytest

from src.extractor import (
    CommentProvider,
    ExtractionResult,
    PostMetadata,
    RateLimitExceededError,
    RawComment,
    extract_usernames,
)
from src import cli


class SimpleProvider:
    def __init__(self, post: PostMetadata) -> None:
        self.post = post

    def fetch_post(self, shortcode: str) -> PostMetadata:
        return self.post


# --- Traversal & Order Tests ---

def test_nested_replies_traversed_immediately_after_parent() -> None:
    comments = [
        RawComment(
            owner_username="userA",
            replies=[
                RawComment(owner_username="userB", replies=[]),
                RawComment(owner_username="userC", replies=[]),
            ],
        ),
        RawComment(
            owner_username="userD",
            replies=[
                RawComment(owner_username="userE", replies=[]),
            ],
        ),
    ]
    post = PostMetadata(shortcode="POST1", owner_username="owner_user", comments=comments)
    result = extract_usernames("POST1", provider=SimpleProvider(post))

    assert result.is_partial is False
    assert result.warning_message is None
    assert result.usernames == ["userA", "userB", "userC", "userD", "userE"]


def test_deeply_nested_replies_traversal() -> None:
    # A -> A1 -> A1_1
    comments = [
        RawComment(
            owner_username="userA",
            replies=[
                RawComment(
                    owner_username="userA1",
                    replies=[
                        RawComment(owner_username="userA1_1", replies=[]),
                    ],
                ),
            ],
        ),
        RawComment(owner_username="userB", replies=[]),
    ]
    post = PostMetadata(shortcode="POST2", owner_username="creator", comments=comments)
    result = extract_usernames("POST2", provider=SimpleProvider(post))

    assert result.usernames == ["userA", "userA1", "userA1_1", "userB"]


def test_no_replies_flag() -> None:
    comments = [
        RawComment(
            owner_username="userA",
            replies=[
                RawComment(owner_username="userB", replies=[]),
            ],
        ),
        RawComment(
            owner_username="userC",
            replies=[
                RawComment(owner_username="userD", replies=[]),
            ],
        ),
    ]
    post = PostMetadata(shortcode="POST3", owner_username="creator", comments=comments)
    result = extract_usernames("POST3", provider=SimpleProvider(post), include_replies=False)

    assert result.usernames == ["userA", "userC"]


# --- Owner Filtering Tests ---

def test_owner_excluded_by_default() -> None:
    comments = [
        RawComment(
            owner_username="post_owner",  # top-level owner comment
            replies=[
                RawComment(owner_username="fan1", replies=[]),  # reply from another user
            ],
        ),
        RawComment(
            owner_username="fan2",
            replies=[
                RawComment(owner_username="post_owner", replies=[]),  # reply from owner
            ],
        ),
    ]
    post = PostMetadata(shortcode="POST4", owner_username="post_owner", comments=comments)
    result = extract_usernames("POST4", provider=SimpleProvider(post), include_owner=False)

    # post_owner must be excluded, but fan1 (reply to owner) and fan2 must be preserved
    assert result.usernames == ["fan1", "fan2"]


def test_owner_included_when_flag_set() -> None:
    comments = [
        RawComment(
            owner_username="post_owner",
            replies=[
                RawComment(owner_username="fan1", replies=[]),
            ],
        ),
        RawComment(
            owner_username="fan2",
            replies=[
                RawComment(owner_username="post_owner", replies=[]),
            ],
        ),
    ]
    post = PostMetadata(shortcode="POST5", owner_username="post_owner", comments=comments)
    result = extract_usernames("POST5", provider=SimpleProvider(post), include_owner=True)

    assert result.usernames == ["post_owner", "fan1", "fan2", "post_owner"]


# --- Rate Limit & Partial Recovery Tests ---

def test_rate_limit_mid_comments_recovers_partial_usernames() -> None:
    def comment_stream() -> Generator[RawComment, None, None]:
        yield RawComment(owner_username="user1", replies=[])
        yield RawComment(owner_username="user2", replies=[])
        yield RawComment(owner_username="user3", replies=[])
        yield RawComment(owner_username="user4", replies=[])
        raise RateLimitExceededError("Instagram rate limit reached (HTTP 429)")

    post = PostMetadata(shortcode="RATE_LIMIT_1", owner_username="creator", comments=comment_stream())
    result = extract_usernames("RATE_LIMIT_1", provider=SimpleProvider(post))

    assert result.is_partial is True
    assert result.usernames == ["user1", "user2", "user3", "user4"]
    assert result.warning_message is not None
    assert "rate limit" in result.warning_message.lower()


def test_rate_limit_mid_replies_recovers_partial_usernames() -> None:
    def reply_stream() -> Generator[RawComment, None, None]:
        yield RawComment(owner_username="reply1", replies=[])
        raise RateLimitExceededError("Rate limit during replies")

    comments = [
        RawComment(owner_username="parent1", replies=reply_stream()),
        RawComment(owner_username="parent2", replies=[]),
    ]
    post = PostMetadata(shortcode="RATE_LIMIT_2", owner_username="creator", comments=comments)
    result = extract_usernames("RATE_LIMIT_2", provider=SimpleProvider(post))

    assert result.is_partial is True
    assert result.usernames == ["parent1", "reply1"]
    assert result.warning_message is not None


# --- CLI Slice 2 Tests ---

def test_cli_flags_include_owner_and_no_replies(tmp_path: Path) -> None:
    comments = [
        RawComment(
            owner_username="owner_user",
            replies=[RawComment(owner_username="reply_user", replies=[])],
        ),
        RawComment(
            owner_username="comment_user",
            replies=[RawComment(owner_username="owner_user", replies=[])],
        ),
    ]
    post = PostMetadata(shortcode="CLITEST", owner_username="owner_user", comments=comments)

    # 1. With --include-owner and --no-replies
    exit_code = cli.main(
        [
            "https://www.instagram.com/p/CLITEST/",
            "--output-dir",
            str(tmp_path),
            "--include-owner",
            "--no-replies",
        ],
        provider=SimpleProvider(post),
    )
    assert exit_code == 0
    file_bytes = (tmp_path / "CLITEST_usernames.txt").read_bytes()
    # Only top-level comments, owner included: owner_user, comment_user
    assert file_bytes == b"owner_user\ncomment_user"


def test_cli_partial_rate_limit_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def comment_stream() -> Generator[RawComment, None, None]:
        yield RawComment(owner_username="user1", replies=[])
        yield RawComment(owner_username="user2", replies=[])
        raise RateLimitExceededError("429 Too Many Requests")

    post = PostMetadata(shortcode="PARTIALCLI", owner_username="creator", comments=comment_stream())
    exit_code = cli.main(
        [
            "https://www.instagram.com/p/PARTIALCLI/",
            "--output-dir",
            str(tmp_path),
        ],
        provider=SimpleProvider(post),
    )
    assert exit_code == 0

    # Output file was written with partial results
    file_bytes = (tmp_path / "PARTIALCLI_usernames.txt").read_bytes()
    assert file_bytes == b"user1\nuser2"

    # Warning was written to stderr
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert "429" in captured.err

