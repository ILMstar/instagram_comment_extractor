"""Slice 3 Tests: Instaloader Provider with Session Support & Exponential Backoff.

Covers:
- Loading session by username
- Loading session by session file
- Transient TooManyRequestsException / ConnectionException during comment iteration retries up to max_retries
- Exhausted retries raises RateLimitExceededError
- Exponential backoff delay calculation without jitter
- Reply pagination retries
- Post fetching transient error retries
- CLI integration with --session-user and --session-file
"""

from pathlib import Path
from unittest.mock import MagicMock, call
import pytest
from instaloader.exceptions import (
    ConnectionException,
    QueryReturnedBadRequestException,
    TooManyRequestsException,
)

from src.extractor import RateLimitExceededError
from src.instaloader_provider import InstaloaderCommentProvider
from src import cli


class StepIterator:
    """Helper iterator that can yield items or raise exceptions in sequence."""

    def __init__(self, steps: list) -> None:
        self.steps = list(steps)
        self.index = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.index >= len(self.steps):
            raise StopIteration
        item = self.steps[self.index]
        self.index += 1
        if isinstance(item, Exception):
            raise item
        return item


class MockComment:
    def __init__(self, username: str, answers: list | None = None) -> None:
        self.owner_username = username
        self.answers = answers or []


class MockPost:
    def __init__(self, shortcode: str, owner: str, comments: list) -> None:
        self.shortcode = shortcode
        self.owner_username = owner
        self._comments = comments

    def get_comments(self):
        return self._comments


# --- Session Loading Tests ---

def test_session_user_loaded() -> None:
    mock_loader = MagicMock()
    provider = InstaloaderCommentProvider(
        session_user="instagram_user",
        loader=mock_loader,
    )
    mock_loader.load_session_from_file.assert_called_once_with("instagram_user")


def test_session_file_loaded(tmp_path: Path) -> None:
    session_file = tmp_path / "custom_session"
    mock_loader = MagicMock()
    provider = InstaloaderCommentProvider(
        session_file=session_file,
        loader=mock_loader,
    )
    mock_loader.load_session_from_file.assert_called_once_with(
        "custom_session",
        filename=str(session_file),
    )


def test_session_file_with_explicit_user(tmp_path: Path) -> None:
    session_file = tmp_path / "custom_session"
    mock_loader = MagicMock()
    provider = InstaloaderCommentProvider(
        session_user="explicit_user",
        session_file=session_file,
        loader=mock_loader,
    )
    mock_loader.load_session_from_file.assert_called_once_with(
        "explicit_user",
        filename=str(session_file),
    )


# --- Retry and Backoff Tests ---

def test_transient_error_retried_and_recovered() -> None:
    comments_stream = StepIterator([
        MockComment("alice"),
        TooManyRequestsException("429 Too Many Requests"),
        MockComment("bob"),
    ])
    mock_post = MockPost("TEST1", "creator", comments_stream)
    sleep_calls: list[float] = []

    provider = InstaloaderCommentProvider(
        max_retries=3,
        base_delay_sec=1.0,
        jitter=False,
        loader=MagicMock(),
        sleep_fn=sleep_calls.append,
        post_loader=lambda ctx, sc: mock_post,
    )

    metadata = provider.fetch_post("TEST1")
    extracted_usernames = [c.owner_username for c in metadata.comments]

    assert extracted_usernames == ["alice", "bob"]
    assert sleep_calls == [1.0]


def test_exhausted_retries_raises_rate_limit_exceeded() -> None:
    comments_stream = StepIterator([
        MockComment("alice"),
        TooManyRequestsException("Rate limit 1"),
        ConnectionException("Connection error 2"),
        QueryReturnedBadRequestException("GraphQL bad request 3"),
        TooManyRequestsException("Final failure 4"),
    ])
    mock_post = MockPost("TEST2", "creator", comments_stream)
    sleep_calls: list[float] = []

    provider = InstaloaderCommentProvider(
        max_retries=3,
        base_delay_sec=2.0,
        jitter=False,
        loader=MagicMock(),
        sleep_fn=sleep_calls.append,
        post_loader=lambda ctx, sc: mock_post,
    )

    metadata = provider.fetch_post("TEST2")
    iterator = iter(metadata.comments)

    first_comment = next(iterator)
    assert first_comment.owner_username == "alice"

    with pytest.raises(RateLimitExceededError) as exc_info:
        next(iterator)

    assert "3 retries" in str(exc_info.value)
    # Exponential backoff delays: 2.0 * 2^0 = 2.0, 2.0 * 2^1 = 4.0, 2.0 * 2^2 = 8.0
    assert sleep_calls == [2.0, 4.0, 8.0]


def test_fetch_post_retries_on_transient_error() -> None:
    mock_post = MockPost("TEST3", "creator", [])
    call_count = 0
    sleep_calls: list[float] = []

    def mock_post_loader(ctx, sc):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TooManyRequestsException("Temporary block")
        return mock_post

    provider = InstaloaderCommentProvider(
        max_retries=3,
        base_delay_sec=0.5,
        jitter=False,
        loader=MagicMock(),
        sleep_fn=sleep_calls.append,
        post_loader=mock_post_loader,
    )

    metadata = provider.fetch_post("TEST3")
    assert metadata.shortcode == "TEST3"
    assert call_count == 3
    assert sleep_calls == [0.5, 1.0]


# --- CLI Flag Tests for Slice 3 ---

def test_cli_wires_session_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    created_provider_kwargs = {}

    class DummyProvider:
        def __init__(self, **kwargs) -> None:
            nonlocal created_provider_kwargs
            created_provider_kwargs = kwargs

        def fetch_post(self, shortcode: str):
            from src.extractor import PostMetadata
            return PostMetadata(shortcode=shortcode, owner_username="owner", comments=[])

    monkeypatch.setattr("src.instagrapi_provider.InstagrapiCommentProvider", DummyProvider)

    session_path = tmp_path / "session_file"
    exit_code = cli.main([
        "https://www.instagram.com/p/CLISESSION/",
        "--output-dir", str(tmp_path),
        "--session-user", "my_user",
        "--session-file", str(session_path),
    ])

    assert exit_code == 0
    assert created_provider_kwargs.get("session_user") == "my_user"
    assert created_provider_kwargs.get("instaloader_session_file") == session_path


