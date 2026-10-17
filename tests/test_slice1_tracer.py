"""Slice 1 Tracer Bullet Tests.

Covers:
- URL parsing and shortcode extraction (p, reel, tv)
- Invalid URL rejection (stories, profile, foreign domains)
- Payload formatting (strict LF, duplicate preservation, no trailing newline)
- File writing (binary LF verification)
- End-to-end CLI invocation with a fake provider
"""

from pathlib import Path
import pytest

from src.url_parser import parse_instagram_url, InvalidInstagramURLError, ParsedPostURL
from src.exporter import format_usernames_payload, write_usernames_file
from src.extractor import extract_usernames, ExtractionResult, PostMetadata, RawComment
from src import cli


class FakeCommentProvider:
    """In-memory stub providing predefined comments for test shortcodes."""

    def __init__(self, posts: dict[str, PostMetadata] | None = None) -> None:
        self.posts = posts or {}

    def fetch_post(self, shortcode: str) -> PostMetadata:
        if shortcode in self.posts:
            return self.posts[shortcode]
        # Default empty post metadata
        return PostMetadata(
            shortcode=shortcode,
            owner_username="post_owner",
            comments=[],
        )


# --- URL Parser Tests ---

@pytest.mark.parametrize(
    "raw_url,expected_shortcode,expected_type,expected_canonical",
    [
        (
            "https://www.instagram.com/p/C8abc123/?igsh=xyz",
            "C8abc123",
            "p",
            "https://www.instagram.com/p/C8abc123/",
        ),
        (
            "https://instagram.com/reel/D9xyz/",
            "D9xyz",
            "reel",
            "https://www.instagram.com/reel/D9xyz/",
        ),
        (
            "http://www.instagram.com/tv/E111/",
            "E111",
            "tv",
            "https://www.instagram.com/tv/E111/",
        ),
        (
            "https://instagram.com/p/C_abc-123_xyz?utm_source=ig_web_copy_link#comments",
            "C_abc-123_xyz",
            "p",
            "https://www.instagram.com/p/C_abc-123_xyz/",
        ),
    ],
)
def test_parse_instagram_url_valid(
    raw_url: str,
    expected_shortcode: str,
    expected_type: str,
    expected_canonical: str,
) -> None:
    parsed = parse_instagram_url(raw_url)
    assert isinstance(parsed, ParsedPostURL)
    assert parsed.shortcode == expected_shortcode
    assert parsed.media_type == expected_type
    assert parsed.canonical_url == expected_canonical


@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://www.instagram.com/stories/someuser/123456789/",
        "https://www.instagram.com/someuser/",
        "https://www.instagram.com/explore/",
        "https://facebook.com/p/C8abc123/",
        "https://google.com",
        "not_a_url",
        "",
        "https://www.instagram.com/p/",
    ],
)
def test_parse_instagram_url_invalid(invalid_url: str) -> None:
    with pytest.raises(InvalidInstagramURLError):
        parse_instagram_url(invalid_url)


# --- Exporter Tests ---

def test_format_usernames_payload_preserves_duplicates_and_no_trailing_newline() -> None:
    usernames = ["alice", "bob", "alice", "charlie", "bob"]
    payload = format_usernames_payload(usernames)
    assert payload == "alice\nbob\nalice\ncharlie\nbob"
    assert not payload.endswith("\n")


def test_format_usernames_payload_empty() -> None:
    assert format_usernames_payload([]) == ""


def test_write_usernames_file_strict_lf(tmp_path: Path) -> None:
    usernames = ["alice", "bob", "alice"]
    output_path = write_usernames_file("TEST123", usernames, tmp_path)
    assert output_path.name == "TEST123_usernames.txt"
    assert output_path.exists()

    # Read binary bytes to verify strict LF line endings (\n without \r)
    content_bytes = output_path.read_bytes()
    assert content_bytes == b"alice\nbob\nalice"
    assert b"\r" not in content_bytes


# --- Extractor Minimal Unit Test for Slice 1 ---

def test_extract_usernames_preserves_duplicates() -> None:
    comments = [
        RawComment(owner_username="user1", replies=[]),
        RawComment(owner_username="user2", replies=[]),
        RawComment(owner_username="user1", replies=[]),
    ]
    post = PostMetadata(
        shortcode="TEST123",
        owner_username="creator",
        comments=comments,
    )
    provider = FakeCommentProvider(posts={"TEST123": post})
    result = extract_usernames("TEST123", provider=provider)

    assert isinstance(result, ExtractionResult)
    assert result.shortcode == "TEST123"
    assert result.usernames == ["user1", "user2", "user1"]
    assert result.is_partial is False


# --- CLI Tracer Bullet Tests ---

def test_cli_main_success(tmp_path: Path) -> None:
    comments = [
        RawComment(owner_username="alice", replies=[]),
        RawComment(owner_username="bob", replies=[]),
        RawComment(owner_username="alice", replies=[]),
    ]
    post = PostMetadata(
        shortcode="TEST123",
        owner_username="creator",
        comments=comments,
    )
    provider = FakeCommentProvider(posts={"TEST123": post})

    exit_code = cli.main(
        ["https://www.instagram.com/p/TEST123/", "--output-dir", str(tmp_path)],
        provider=provider,
    )
    assert exit_code == 0

    expected_file = tmp_path / "TEST123_usernames.txt"
    assert expected_file.exists()
    content_bytes = expected_file.read_bytes()
    assert content_bytes == b"alice\nbob\nalice"
    assert b"\r" not in content_bytes


def test_cli_main_invalid_url(tmp_path: Path) -> None:
    provider = FakeCommentProvider()
    exit_code = cli.main(
        ["https://www.instagram.com/stories/alice/123/", "--output-dir", str(tmp_path)],
        provider=provider,
    )
    assert exit_code != 0

