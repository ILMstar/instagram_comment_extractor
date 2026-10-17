"""Slice 4 Tests: FastAPI Local Web UI & Direct .txt Download.

Covers:
- GET / serves HTML single-page UI with form controls
- POST /api/download with valid URL returns 200, Content-Type, Content-Disposition, and LF body
- POST /api/download with invalid URL returns 400 Bad Request
- POST /api/extract returns JSON preview metadata
- Respects include_owner and include_replies options
- Auto-saves extracted usernames to output directory
"""

from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from src.extractor import PostMetadata, RawComment
from src.web import create_app


class FakeCommentProvider:
    def __init__(self, posts: dict[str, PostMetadata] | None = None) -> None:
        self.posts = posts or {}

    def fetch_post(self, shortcode: str) -> PostMetadata:
        if shortcode in self.posts:
            return self.posts[shortcode]
        return PostMetadata(
            shortcode=shortcode,
            owner_username="creator",
            comments=[],
        )


@pytest.fixture
def test_client() -> TestClient:
    comments = [
        RawComment(
            owner_username="user1",
            replies=[RawComment(owner_username="reply1", replies=[])],
        ),
        RawComment(
            owner_username="creator",
            replies=[],
        ),
        RawComment(
            owner_username="user1",
            replies=[],
        ),
    ]
    post = PostMetadata(shortcode="WEBPOST123", owner_username="creator", comments=comments)
    provider = FakeCommentProvider(posts={"WEBPOST123": post})
    app = create_app(provider=provider)
    return TestClient(app)


def test_get_root_serves_html(test_client: TestClient) -> None:
    response = test_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text.lower()
    assert "instagram comment" in html
    assert "form" in html or "input" in html


def test_post_api_download_valid_json(test_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.web.DEFAULT_OUTPUT_DIR", tmp_path)

    response = test_client.post(
        "/api/download",
        json={
            "url": "https://www.instagram.com/p/WEBPOST123/",
            "include_owner": False,
            "include_replies": True,
        },
    )

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"].lower()
    assert 'attachment; filename="WEBPOST123_usernames.txt"' in response.headers["content-disposition"]
    # user1, reply1, user1 (creator excluded, duplicate preserved)
    assert response.text == "user1\nreply1\nuser1"

    # Verify file saved to output directory
    saved_file = tmp_path / "WEBPOST123_usernames.txt"
    assert saved_file.exists()
    assert saved_file.read_bytes() == b"user1\nreply1\nuser1"


def test_post_api_download_valid_form_data(test_client: TestClient) -> None:
    response = test_client.post(
        "/api/download",
        data={
            "url": "https://www.instagram.com/p/WEBPOST123/",
            "include_owner": "true",
            "include_replies": "false",
        },
    )

    assert response.status_code == 200
    # Top-level only, owner included: user1, creator, user1
    assert response.text == "user1\ncreator\nuser1"


def test_post_api_download_invalid_url(test_client: TestClient) -> None:
    response = test_client.post(
        "/api/download",
        json={"url": "https://www.instagram.com/stories/user/123/"},
    )
    assert response.status_code == 400
    assert "detail" in response.json()


def test_post_api_extract_preview(test_client: TestClient) -> None:
    response = test_client.post(
        "/api/extract",
        json={
            "url": "https://www.instagram.com/p/WEBPOST123/",
            "include_owner": False,
            "include_replies": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["shortcode"] == "WEBPOST123"
    assert data["count"] == 3
    assert data["is_partial"] is False
    assert data["usernames"] == ["user1", "reply1", "user1"]


def test_post_api_extract_login_required() -> None:
    from instaloader.exceptions import LoginRequiredException

    class AuthRequiredProvider:
        def fetch_post(self, shortcode: str):
            raise LoginRequiredException("Login required to access comments of a post.")

    app = create_app(provider=AuthRequiredProvider())
    client = TestClient(app)

    response = client.post(
        "/api/extract",
        json={"url": "https://www.instagram.com/p/TESTAUTH/"},
    )
    assert response.status_code == 401
    assert "session" in response.json()["detail"].lower()


