"""Instaloader comment provider adapter.

Adapts instaloader.Instaloader and instaloader.Post into the CommentProvider protocol,
supporting session authentication, lazy comment pagination, and exponential backoff
retries with jitter on rate limits.
"""

import logging
from pathlib import Path
import random
import time
from typing import Any, Callable, Generator, Iterable

import instaloader
from instaloader.exceptions import (
    ConnectionException,
    QueryReturnedBadRequestException,
    TooManyRequestsException,
)

from src.extractor import CommentProvider, PostMetadata, RateLimitExceededError, RawComment

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (
    TooManyRequestsException,
    ConnectionException,
    QueryReturnedBadRequestException,
)


def _get_comments_graphql_fallback(post: Any) -> Iterable[Any]:
    """Directly uses the GraphQL NodeIterator for comment pagination.

    Instaloader falls back to the iPhone API endpoint (i.instagram.com/api/v1)
    for posts with many comments, but that endpoint frequently rejects sessions
    created from browser cookies. This function forces the standard web GraphQL
    endpoint which is more reliable.
    """
    from datetime import datetime

    context = post._context

    def _postcommentanswer(node: dict) -> Any:
        return instaloader.PostCommentAnswer(
            id=int(node["id"]),
            created_at_utc=datetime.utcfromtimestamp(node["created_at"]),
            text=node["text"],
            owner=instaloader.Profile(context, node["owner"]),
            likes_count=node.get("edge_liked_by", {}).get("count", 0),
        )

    def _postcommentanswers(node: dict) -> Iterable[Any]:
        if "edge_threaded_comments" not in node:
            return
        answer_count = node["edge_threaded_comments"]["count"]
        if answer_count == 0:
            return
        answer_edges = node["edge_threaded_comments"]["edges"]
        if answer_count == len(answer_edges):
            yield from (_postcommentanswer(comment["node"]) for comment in answer_edges)
            return
        yield from instaloader.NodeIterator(
            context,
            "51fdd02b67508306ad4484ff574a0b62",
            lambda d: d["data"]["comment"]["edge_threaded_comments"],
            _postcommentanswer,
            {"comment_id": node["id"]},
            f"https://www.instagram.com/p/{post.shortcode}/",
        )

    def _postcomment(node: dict) -> Any:
        return instaloader.PostComment(
            context=context, node=node, answers=_postcommentanswers(node), post=post
        )

    return instaloader.NodeIterator(
        context,
        "97b41c52301f77ce508f55e66d17620e",
        lambda d: d["data"]["shortcode_media"]["edge_media_to_parent_comment"],
        _postcomment,
        {"shortcode": post.shortcode},
        f"https://www.instagram.com/p/{post.shortcode}/",
    )


class InstaloaderCommentProvider(CommentProvider):
    """Adapts Instaloader into the CommentProvider protocol with backoff retries."""

    def __init__(
        self,
        session_user: str | None = None,
        session_file: Path | None = None,
        max_retries: int = 5,
        base_delay_sec: float = 3.0,
        loader: Any | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        jitter: bool = False,
        post_loader: Callable[[Any, str], Any] | None = None,
    ) -> None:
        self.session_user = session_user
        self.session_file = session_file
        self.max_retries = max_retries
        self.base_delay_sec = base_delay_sec
        self.sleep_fn = sleep_fn
        self.jitter = jitter
        self.loader = loader or instaloader.Instaloader()
        self._post_loader = post_loader or instaloader.Post.from_shortcode

        if session_file is not None:
            user = session_user or Path(session_file).stem
            self.loader.load_session_from_file(user, filename=str(session_file))
        elif session_user is not None:
            self.loader.load_session_from_file(session_user)

    def _calculate_backoff_delay(self, attempt: int) -> float:
        delay = self.base_delay_sec * (2 ** (attempt - 1))
        if self.jitter:
            delay += random.uniform(0.0, 0.1 * delay)
        return delay

    def _retry_call(self, fn: Callable[[], Any], description: str = "operation") -> Any:
        """Retries a callable with exponential backoff on transient Instagram errors."""
        retries = 0
        while True:
            try:
                return fn()
            except RETRYABLE_EXCEPTIONS as exc:
                retries += 1
                if retries > self.max_retries:
                    raise RateLimitExceededError(
                        f"{description} failed after {self.max_retries} retries: {exc}"
                    ) from exc
                delay = self._calculate_backoff_delay(retries)
                self.sleep_fn(delay)

    def _paginate_with_retries(self, iterable: Iterable[Any]) -> Generator[Any, None, None]:
        it = iter(iterable)
        retries = 0
        while True:
            try:
                item = next(it)
                retries = 0
                yield item
            except StopIteration:
                break
            except RETRYABLE_EXCEPTIONS as exc:
                retries += 1
                if retries > self.max_retries:
                    raise RateLimitExceededError(
                        f"Instagram rate limit reached after {self.max_retries} retries: {exc}"
                    ) from exc
                delay = self._calculate_backoff_delay(retries)
                self.sleep_fn(delay)

    def _to_raw_comment(self, comment: Any) -> RawComment:
        username = getattr(comment, "owner_username", None)
        if not username and hasattr(comment, "owner"):
            username = getattr(comment.owner, "username", str(comment.owner))
        if not username:
            username = "unknown"

        answers_attr = getattr(comment, "answers", None)
        if answers_attr is not None:
            iterable_answers = answers_attr() if callable(answers_attr) else answers_attr

            def reply_generator() -> Generator[RawComment, None, None]:
                for answer in self._paginate_with_retries(iterable_answers):
                    yield self._to_raw_comment(answer)

            replies: Iterable[RawComment] = reply_generator()
        else:
            replies = []

        return RawComment(owner_username=username, replies=replies)

    def _get_comments_with_fallback(self, post: Any, shortcode: str) -> Iterable[Any]:
        """Tries normal get_comments(), falls back to GraphQL if iPhone API fails.

        If post is a real instaloader.Post with comments > 12, instaloader's get_comments()
        would always invoke the iPhone API endpoint which rejects browser cookie sessions.
        In that case, we directly use the GraphQL endpoint to avoid a 90-second retry timeout.
        """
        if hasattr(post, "_context") and getattr(post, "comments", 0) > instaloader.NodeIterator.page_length():
            logger.info("Post %s has >12 comments; using GraphQL pagination directly", shortcode)
            return self._retry_call(
                lambda: _get_comments_graphql_fallback(post),
                description=f"Fetching comments for {shortcode} via GraphQL",
            )

        try:
            return self._retry_call(
                lambda: (
                    post.get_comments()
                    if callable(getattr(post, "get_comments", None))
                    else getattr(post, "comments", [])
                ),
                description=f"Fetching comments for {shortcode}",
            )
        except RateLimitExceededError:
            logger.info(
                "iPhone API failed for %s, falling back to GraphQL pagination",
                shortcode,
            )
            return self._retry_call(
                lambda: _get_comments_graphql_fallback(post),
                description=f"Fetching comments for {shortcode} via GraphQL fallback",
            )

    def fetch_post(self, shortcode: str) -> PostMetadata:
        """Fetches post metadata and establishes a lazy comment pagination stream."""
        post = self._retry_call(
            lambda: self._post_loader(self.loader.context, shortcode),
            description=f"Fetching post {shortcode}",
        )

        owner_username = (
            getattr(post, "owner_username", None)
            or getattr(getattr(post, "owner_profile", None), "username", "")
            or ""
        )

        def comment_generator() -> Generator[RawComment, None, None]:
            comments_iterable = self._get_comments_with_fallback(post, shortcode)
            for comment in self._paginate_with_retries(comments_iterable):
                yield self._to_raw_comment(comment)

        return PostMetadata(
            shortcode=shortcode,
            owner_username=owner_username,
            comments=comment_generator(),
        )
