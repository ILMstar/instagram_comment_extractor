# Implementation Plan: Instagram Comment Username Extractor

## 1. Architectural Overview & System Invariants
- **Goal:** Build a dual-interface (CLI + local FastAPI Web UI) Instagram comment username extractor that takes a post/Reel/TV URL, paginates through all top-level and nested comments using an authenticated session, preserves duplicate usernames in occurrence order, excludes the post author by default, and outputs a strict `\n`-delimited `.txt` file.
- **Core Stack & Environment:** Python 3.11+, `instaloader` (GraphQL comment pagination & session handling), `FastAPI` + `uvicorn` (local web server & static UI), `pytest` + `httpx` (test runner & API test client).
- **System Invariants:**
  1. **Zero Network Calls in Unit/Slice Tests:** All comment extraction logic must depend on an injected `CommentProvider` protocol so tests run deterministically offline.
  2. **Strict Duplicate Preservation:** The pipeline must never deduplicate (`set()`) extracted usernames; if a user comments 5 times, their username appears 5 times in exact traversal order.
  3. **Graceful Partial Recovery:** On unrecoverable rate-limit (`429`) or GraphQL pagination failure after retries, the extractor must flush all usernames collected so far rather than discarding progress.
  4. **Strict Formatting:** Output files must use LF (`\n`) line endings, contain one raw username per line (no `@` prefix), and have no trailing blank line at EOF.

---

## 2. Deep Module Contracts & Interfaces
Define the public boundaries between modules before implementation. Do not hardcode brittle line numbers.

### Module: `src/url_parser.py`
- **Responsibility:** Validates Instagram URLs, strips tracking parameters (`?igsh=...`, `?utm_source=...`), rejects unsupported links (Stories, profiles), and extracts the canonical `shortcode`.
- **Public Interface / Signatures:**
  ```python
  from dataclasses import dataclass

  class InvalidInstagramURLError(ValueError):
      """Raised when a URL is not a valid Instagram Post, Reel, or TV link."""

  @dataclass(frozen=True)
  class ParsedPostURL:
      shortcode: str
      canonical_url: str
      media_type: str  # "p" | "reel" | "tv"

  def parse_instagram_url(raw_url: str) -> ParsedPostURL:
      """Extracts shortcode and media type or raises InvalidInstagramURLError."""
  ```
- **Testing Seam:** Pure function taking a `str` and returning a frozen `ParsedPostURL` value object or raising `InvalidInstagramURLError`.

### Module: `src/extractor.py`
- **Responsibility:** Orchestrates comment traversal (top-level + nested replies), filters out the post owner unless `include_owner=True`, handles exponential backoff retries on rate-limit errors, and yields/collects usernames with partial-recovery metadata.
- **Public Interface / Signatures:**
  ```python
  from dataclasses import dataclass
  from typing import Iterable, Protocol, List

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
  ```
- **Testing Seam:** Pass an in-memory `FakeCommentProvider` (including generators that raise `RateLimitExceededError` mid-iteration) into `extract_usernames()` without touching `instaloader` or the network.

### Module: `src/instaloader_provider.py`
- **Responsibility:** Adapts `instaloader.Instaloader` and `instaloader.Post` into the `CommentProvider` protocol, encapsulating cookie/session loading and translating `TooManyRequestsException` / `QueryReturnedBadRequestException` into retryable backoff or `RateLimitExceededError`.
- **Public Interface / Signatures:**
  ```python
  from pathlib import Path
  from src.extractor import CommentProvider, PostMetadata

  class InstaloaderCommentProvider(CommentProvider):
      def __init__(
          self,
          session_user: str | None = None,
          session_file: Path | None = None,
          max_retries: int = 3,
          base_delay_sec: float = 1.0,
      ) -> None: ...

      def fetch_post(self, shortcode: str) -> PostMetadata: ...
  ```
- **Testing Seam:** Test retry/backoff mechanics by injecting a stubbed loader factory and zero-delay sleep function.

### Module: `src/exporter.py`
- **Responsibility:** Serializes `ExtractionResult.usernames` into a strict `\n`-delimited payload and writes it atomically to `output/<shortcode>_usernames.txt`.
- **Public Interface / Signatures:**
  ```python
  from pathlib import Path
  from typing import Sequence

  def format_usernames_payload(usernames: Sequence[str]) -> str:
      """Joins usernames with '\\n' with no trailing newline."""

  def write_usernames_file(shortcode: str, usernames: Sequence[str], output_dir: Path) -> Path:
      """Writes <shortcode>_usernames.txt with strict LF endings and returns the file path."""
  ```
- **Testing Seam:** Pure string formatter + filesystem writer testable via `pytest`'s `tmp_path` fixture.

### Module: `src/web.py` & `src/cli.py`
- **Responsibility:** Exposes the pipeline via a FastAPI Web UI (`GET /`, `POST /api/extract`, `POST /api/download`) and a terminal CLI (`python -m src.cli <url>`).
- **Public Interface / Signatures:**
  ```python
  from fastapi import FastAPI
  from src.extractor import CommentProvider

  def create_app(provider: CommentProvider | None = None) -> FastAPI:
      """Factory function allowing test suites to inject a FakeCommentProvider."""
  ```
- **Testing Seam:** `fastapi.testclient.TestClient(create_app(provider=FakeCommentProvider()))`.

---

## 3. Target Workspace File Tree
Mark every file the Antigravity agent will create, modify, or remove:

- `[NEW] requirements.txt` - Project dependencies (`instaloader`, `fastapi`, `uvicorn`, `pytest`, `httpx`)
- `[NEW] pytest.ini` - Pytest configuration setting `pythonpath = .`
- `[NEW] src/__init__.py` - Package marker
- `[NEW] src/url_parser.py` - URL validation and shortcode extraction module
- `[NEW] src/extractor.py` - Core comment traversal, owner exclusion, and partial recovery engine
- `[NEW] src/exporter.py` - `\n`-delimited text formatter and file writer
- `[NEW] src/instaloader_provider.py` - Instaloader adapter with session support and exponential backoff
- `[NEW] src/cli.py` - Command-line interface entry point
- `[NEW] src/web.py` - FastAPI application and static single-page UI
- `[NEW] tests/test_slice1_tracer.py` - E2E tracer bullet tests for URL parsing, formatting, and CLI flow
- `[NEW] tests/test_slice2_extractor.py` - Deep unit tests for nested replies, owner filtering, and rate-limit recovery
- `[NEW] tests/test_slice3_provider.py` - Adapter tests for backoff retries and exception translation
- `[NEW] tests/test_slice4_web.py` - FastAPI endpoint and downloadable `.txt` response tests

---

## 4. Tracer-Bullet Vertical Slices (Execution Checklist)
Execute slices strictly in order. Do not advance to the next slice until the current slice's verification command passes.

- **`[AFK]` (Away From Keyboard):** Safe for Antigravity to implement, test, and verify autonomously end-to-end.
- **`[HITL]` (Human-In-The-Loop):** Pause after verification so the developer can inspect architectural seams, UI behavior, or security boundaries.

### Slice 1: Minimal E2E Tracer Bullet (URL -> Extractor -> `.txt` File via CLI) `[AFK]`
- **Objective:** Establish the project skeleton, parse `/p/`, `/reel/`, and `/tv/` URLs, extract top-level comments while preserving duplicates, and write `<shortcode>_usernames.txt` via the CLI.
- **Files Touched:** `requirements.txt`, `pytest.ini`, `src/__init__.py`, `src/url_parser.py`, `src/extractor.py`, `src/exporter.py`, `src/cli.py`, `tests/test_slice1_tracer.py`
- **TDD Implementation Steps:**
  - [x] 1. Create `requirements.txt` and `pytest.ini`. Write failing tests in `tests/test_slice1_tracer.py` asserting:
    - `parse_instagram_url` extracts shortcodes from `https://www.instagram.com/p/C8abc123/?igsh=xyz`, `/reel/D9xyz/`, and `/tv/E111/`, and raises `InvalidInstagramURLError` on `/stories/` or profile URLs.
    - `format_usernames_payload(["alice", "bob", "alice"])` outputs `"alice\nbob\nalice"`.
    - `cli.main(["https://www.instagram.com/p/TEST123/", "--output-dir", str(tmp_path)], provider=fake_provider)` writes `TEST123_usernames.txt` with exact `\n` line endings.
  - [x] 2. Implement `src/url_parser.py`, minimal `src/extractor.py` (top-level comments), `src/exporter.py` (with `newline="\n"`), and `src/cli.py`.
  - [x] 3. Verify all tests pass with zero warnings.
- **Verification Command:**
  ```bash
  pytest tests/test_slice1_tracer.py -v
  ```
- **Expected Output / Acceptance Criteria:** All tests in `test_slice1_tracer.py` pass; output file contains duplicate usernames in exact order separated by `\n` with no trailing blank line.

### Slice 2: Nested Replies, Post Owner Exclusion & Partial Rate-Limit Recovery `[AFK]`
- **Objective:** Expand `src/extractor.py` to traverse nested comment replies in order, filter out the post owner's username by default (`include_owner=False`), and gracefully preserve partial username lists when `RateLimitExceededError` occurs mid-stream.
- **Files Touched:** `src/extractor.py`, `src/cli.py`, `tests/test_slice2_extractor.py`
- **TDD Implementation Steps:**
  - [x] 1. Write failing tests in `tests/test_slice2_extractor.py` asserting:
    - Nested replies are extracted immediately after their parent comment in exact order.
    - Comments/replies authored by `post.owner_username` are excluded when `include_owner=False` and retained when `include_owner=True`.
    - When a comment generator raises `RateLimitExceededError` after yielding 4 comments, `extract_usernames` returns an `ExtractionResult` with those 4 usernames, `is_partial=True`, and a populated `warning_message`.
  - [x] 2. Implement full traversal, owner filtering, and `try / except RateLimitExceededError` recovery inside `src/extractor.py`.
  - [x] 3. Expose `--include-owner` and `--no-replies` flags in `src/cli.py` and print warnings to `stderr` if `result.is_partial` is True.
- **Verification Command:**
  ```bash
  pytest tests/test_slice1_tracer.py tests/test_slice2_extractor.py -v
  ```
- **Expected Output / Acceptance Criteria:** All unit and CLI tests pass, confirming duplicate preservation across nested threads and partial state recovery on mid-stream interruption.

### Slice 3: Instaloader Provider with Session Support & Exponential Backoff `[AFK]`
- **Objective:** Implement `InstaloaderCommentProvider` to connect to Instagram, load optional browser/CLI session files, paginate comments/replies, retry transient `429`/GraphQL errors with exponential backoff + jitter, and raise `RateLimitExceededError` when retries are exhausted.
- **Files Touched:** `src/instaloader_provider.py`, `src/cli.py`, `tests/test_slice3_provider.py`
- **TDD Implementation Steps:**
  - [x] 1. Write failing tests in `tests/test_slice3_provider.py` using a mock Instaloader instance asserting:
    - Transient `ConnectionException` / `TooManyRequestsException` during comment iteration triggers up to `max_retries` backoff attempts before raising `RateLimitExceededError`.
    - Session files or username sessions are loaded when provided.
  - [x] 2. Implement `src/instaloader_provider.py` wrapping `instaloader.Post.from_shortcode` and yielding `RawComment` items lazily so mid-pagination blocks still allow `extractor.py` to keep already-yielded items.
  - [x] 3. Wire `InstaloaderCommentProvider` as the default provider in `src/cli.py` (supporting `--session-user` and `--session-file` arguments).
- **Verification Command:**
  ```bash
  pytest tests/test_slice1_tracer.py tests/test_slice2_extractor.py tests/test_slice3_provider.py -v
  ```
- **Expected Output / Acceptance Criteria:** All provider retry and lazy-iteration tests pass without making live requests to Instagram.

### Slice 4: FastAPI Local Web UI & Direct `.txt` Download `[HITL]`
- **Objective:** Build the local FastAPI application (`src/web.py`) serving a clean single-page UI where the user pastes an Instagram post link, toggles options, previews the extracted count/warnings, and downloads `<shortcode>_usernames.txt`.
- **Files Touched:** `src/web.py`, `tests/test_slice4_web.py`
- **TDD Implementation Steps:**
  - [x] 1. Write failing tests in `tests/test_slice4_web.py` using `FastAPI.TestClient` asserting:
    - `GET /` returns `200 OK` with HTML containing the URL input form.
    - `POST /api/download` with a valid Instagram URL returns `200 OK`, `Content-Type: text/plain; charset=utf-8`, `Content-Disposition: attachment; filename="<shortcode>_usernames.txt"`, and the exact `\n`-separated username body.
    - `POST /api/download` with an invalid URL returns `400 Bad Request` with a clear error message.
  - [x] 2. Implement `src/web.py` with `create_app()`, endpoints, automatic saving to `output/<shortcode>_usernames.txt`, and an embedded dark-mode HTML/JS interface.
  - [x] 3. Run the full test suite to verify all slices together.
- **Verification Command:**
  ```bash
  pytest -v
  ```
- **Expected Output / Acceptance Criteria:** All 4 test suites pass (`100%` green). Pause for human review (`[HITL]`) so the developer can launch `uvicorn src.web:app --reload` and test pasting a live Instagram link in the browser.