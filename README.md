# Instagram Comment Username Extractor

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)
![Tests](https://img.shields.io/badge/tests-pytest-brightgreen.svg)

A dual-interface (**CLI** + **Local FastAPI Web UI**) tool that extracts commenter usernames from Instagram Posts (`/p/`), Reels (`/reel/`), and IGTV (`/tv/`) links. Built on top of `instaloader`, it paginates through top-level comments and nested replies using an authenticated session, preserves exact duplicate entry counts for giveaways or analytics, and exports clean, strictly formatted `.txt` files.

---

## Key Features & System Guarantees

* **Dual Interface:** Run batch extractions from your terminal via the CLI or launch the built-in dark-mode FastAPI Web UI for interactive previews and one-click `.txt` downloads.
* **Strict Duplicate Preservation:** Never deduplicates usernames (`set()` is never used). If a user comments 5 times, their username appears 5 times in exact traversal order—ideal for giveaway entries and engagement tracking.
* **Top-Level & Nested Reply Traversal:** Recursively extracts nested thread replies immediately following their parent comment (configurable via flags).
* **Automatic Post Author Exclusion:** Excludes the post creator's own comments and replies by default so they don't skew your list (can be toggled on if needed).
* **Exponential Backoff & Graceful Partial Recovery:** Automatically retries transient Instagram `429 Too Many Requests` or GraphQL pagination blocks with exponential backoff and jitter. If Instagram hard-blocks further pagination after all retries are exhausted, the extractor **never discards your progress**—it safely flushes all usernames collected up to that point and flags the output as a partial extraction.
* **Strict Output Formatting:** Produces `output/<shortcode>_usernames.txt` using strict LF (`\n`) line endings, one raw username per line (no `@` prefix), and zero trailing blank lines at EOF.
* **100% Offline Deterministic Test Suite:** Built around a decoupled `CommentProvider` protocol so unit and end-to-end slice tests execute rapidly with zero live network calls.

---

## Project Structure

```text
instagram_comment_extractor/
├── requirements.txt              # Project dependencies (instaloader, fastapi, uvicorn, pytest, httpx)
├── pytest.ini                    # Pytest configuration (pythonpath = .)
├── src/
│   ├── __init__.py               # Package marker
│   ├── url_parser.py             # URL validation, tracking param stripping, & shortcode extraction
│   ├── extractor.py              # Core traversal, owner filtering, & partial rate-limit recovery engine
│   ├── instaloader_provider.py   # Instaloader adapter with session handling & exponential backoff
│   ├── exporter.py               # Strict LF (\n) text serializer & atomic file writer
│   ├── cli.py                    # Terminal command-line interface entry point
│   └── web.py                    # FastAPI server & embedded single-page dark-mode Web UI
└── tests/
    ├── test_slice1_tracer.py     # E2E tests for URL parsing, formatting, and CLI flow
    ├── test_slice2_extractor.py  # Unit tests for nested replies, owner filtering, and partial recovery
    ├── test_slice3_provider.py   # Adapter tests for backoff retries and exception translation
    └── test_slice4_web.py        # FastAPI endpoint and downloadable .txt response tests
```

---

## Prerequisites & Installation

### 1. Clone the Repository
```bash
git clone [https://github.com/ILMstar/instagram_comment_extractor.git](https://github.com/ILMstar/instagram_comment_extractor.git)
cd instagram_comment_extractor
```

### 2. Create a Virtual Environment & Install Dependencies
Requires **Python 3.11+**.

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows:
.venv\Scripts\activate
# On macOS / Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Authentication & Session Setup (Recommended)

Instagram heavily restricts unauthenticated GraphQL pagination for comments. To extract large comment threads reliably without immediate rate-limiting, authenticate via `instaloader` once before running the tool:

```bash
instaloader --login YOUR_INSTAGRAM_USERNAME
```

This saves an encrypted session file locally on your machine, which you can pass to the extractor via `--session-user` or `--session-file`.

---

## Usage

### Option 1: Local FastAPI Web UI

Start the local web server using `uvicorn`:

```bash
uvicorn src.web:app --reload
```

1. Open your browser and navigate to **`http://127.0.0.1:8000`**.
2. Paste any valid Instagram Post (`/p/...`), Reel (`/reel/...`), or IGTV (`/tv/...`) URL (tracking parameters like `?igsh=...` or `?utm_source=...` are stripped automatically).
3. Toggle extraction options (e.g., whether to include the post owner or nested replies).
4. Preview the extracted username count and any rate-limit warnings directly in the browser, or click **Download `.txt`** to save `<shortcode>_usernames.txt` (also saved automatically to the local `output/` directory).

#### Web API Endpoints
If you want to call the local server programmatically:
* `GET /` — Serves the interactive single-page HTML/JS interface.
* `POST /api/extract` — Runs extraction and returns JSON metadata (shortcode, username count, preview list, `is_partial` flag, and warning messages).
* `POST /api/download` — Runs extraction, writes `output/<shortcode>_usernames.txt`, and streams the file back with `Content-Type: text/plain; charset=utf-8` and `Content-Disposition: attachment; filename="<shortcode>_usernames.txt"`. Returns `400 Bad Request` on invalid/unsupported URLs (such as Stories or profile links).

---

### Option 2: Command-Line Interface (CLI)

Run the extractor directly from your terminal using `python -m src.cli`:

#### Basic Extraction
```bash
python -m src.cli "[https://www.instagram.com/p/C8abc123/?igsh=xyz](https://www.instagram.com/p/C8abc123/?igsh=xyz)"
```

#### Authenticated Extraction (Recommended for Large Posts)
```bash
# Using a saved Instaloader username session
python -m src.cli "[https://www.instagram.com/reel/D9xyz/](https://www.instagram.com/reel/D9xyz/)" --session-user your_ig_username

# Or pointing directly to a session file path
python -m src.cli "[https://www.instagram.com/reel/D9xyz/](https://www.instagram.com/reel/D9xyz/)" --session-file /path/to/session-file
```

#### Customizing Output & Filtering Flags
```bash
python -m src.cli "[https://www.instagram.com/p/C8abc123/](https://www.instagram.com/p/C8abc123/)" \
  --output-dir ./my_exports \
  --include-owner \
  --no-replies
```

#### CLI Arguments Reference

| Argument / Flag | Description | Default |
| :--- | :--- | :--- |
| `url` | **(Required)** Instagram Post (`/p/`), Reel (`/reel/`), or TV (`/tv/`) URL. | — |
| `--output-dir` | Directory where `<shortcode>_usernames.txt` will be written. | `output/` |
| `--include-owner` | Include comments and replies written by the post's author. | `False` (Excluded) |
| `--no-replies` | Skip nested comment replies and extract top-level comments only. | `False` (Replies included) |
| `--session-user` | Instagram username for loading a locally saved `instaloader` session. | `None` |
| `--session-file` | Explicit path to an `instaloader` session file. | `None` |

---

## Output File Format

Extracted usernames are saved to `output/<shortcode>_usernames.txt`.

**Example (`output/C8abc123_usernames.txt`):**
```text
alice_wonder
bob_builder
alice_wonder
charlie_99
```

**Formatting Guarantees:**
1. **No `@` symbols:** Only raw Instagram handles are written.
2. **Exact traversal order & duplicates kept:** Notice `alice_wonder` appears twice above because she commented twice.
3. **Strict LF (`\n`) line endings:** Even on Windows, files are written with `\n` line endings and **no trailing blank line** at the end of the file, ensuring compatibility with line-counting tools (`wc -l`), random picker scripts, and spreadsheet imports.

---

## Architecture & Module Contracts

The codebase is architected around deep modules with clean dependency injection boundaries:

1. **`src/url_parser.py` (`parse_instagram_url`)**
   * Validates raw URLs, strips tracking query strings, rejects unsupported routes (such as `/stories/` or profile URLs) by raising `InvalidInstagramURLError`, and returns an immutable `ParsedPostURL(shortcode, canonical_url, media_type)` value object.
2. **`src/extractor.py` (`extract_usernames` & `CommentProvider` Protocol)**
   * Defines the `CommentProvider` protocol and `PostMetadata` / `RawComment` data structures.
   * Traverses top-level comments and nested replies lazily, filters out `post.owner_username` unless `include_owner=True`, and catches `RateLimitExceededError` mid-stream to return an `ExtractionResult` containing all usernames gathered prior to the block (`is_partial=True`).
3. **`src/instaloader_provider.py` (`InstaloaderCommentProvider`)**
   * Concrete implementation of `CommentProvider` wrapping `instaloader.Instaloader` and `instaloader.Post.from_shortcode`.
   * Manages session authentication and translates `TooManyRequestsException`, `QueryReturnedBadRequestException`, and `ConnectionException` into exponential backoff retries before finally raising `RateLimitExceededError`.
4. **`src/exporter.py` (`format_usernames_payload` & `write_usernames_file`)**
   * Pure payload formatter and file writer that guarantees strict `\n` separation with no trailing newline.
5. **`src/cli.py` & `src/web.py`**
   * Thin delivery mechanisms that wire user inputs to the core extraction pipeline and allow injecting a `FakeCommentProvider` during automated testing.

---

## Running the Test Suite

All unit, integration, and web tests use injected `FakeCommentProvider` fakes and mock loaders—meaning **zero live network calls** are made to Instagram during testing.

Run the complete test suite with `pytest`:

```bash
pytest -v
```

Or run individual vertical test slices:

```bash
# Slice 1: URL parsing, payload formatting, and CLI E2E tracer flow
pytest tests/test_slice1_tracer.py -v

# Slice 2: Nested reply order, post owner filtering, and partial rate-limit recovery
pytest tests/test_slice2_extractor.py -v

# Slice 3: Instaloader session loading, exponential backoff retries, and exception translation
pytest tests/test_slice3_provider.py -v

# Slice 4: FastAPI Web UI routes, file downloads, and error handling
pytest tests/test_slice4_web.py -v
```

---

## Troubleshooting

* **`InvalidInstagramURLError` / `400 Bad Request`:** Make sure the link is a direct Instagram Post (`/p/`), Reel (`/reel/`), or TV (`/tv/`) URL. Instagram Stories (`/stories/`) and profile pages do not have standard public comment threads and are intentionally rejected.
* **Partial Extraction Warning (`is_partial=True`):** Instagram temporarily rate-limited your IP or session mid-pagination after exhausting backoff retries. All usernames extracted prior to the block have already been saved to `output/<shortcode>_usernames.txt`. To extract the full list on high-comment posts, ensure you are passing an authenticated session via `--session-user` or `--session-file`.
