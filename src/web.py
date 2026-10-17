"""FastAPI web application for Instagram comment extraction.

Provides a clean local UI and REST endpoints to extract and download comment
usernames as strict LF-delimited .txt files.
"""

from pathlib import Path
import sys
from typing import Any
from urllib.parse import parse_qs

# Ensure project root is in sys.path when launched from other directories
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from instaloader.exceptions import InstaloaderException, LoginRequiredException

from src.exporter import format_usernames_payload, write_usernames_file
from src.extractor import CommentProvider, RateLimitExceededError, extract_usernames
from src.instaloader_provider import InstaloaderCommentProvider
from src.url_parser import InvalidInstagramURLError, parse_instagram_url

DEFAULT_OUTPUT_DIR = Path("output")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Instagram Comment Username Extractor</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --accent-hover: #0284c7;
      --border: #334155;
      --danger: #ef4444;
      --warning: #f59e0b;
      --success: #10b981;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background-color: var(--bg); color: var(--text); display: flex; justify-content: center; padding: 2.5rem 1rem; min-height: 100vh; }
    .container { width: 100%; max-width: 640px; }
    header { text-align: center; margin-bottom: 2rem; }
    header h1 { font-size: 1.875rem; font-weight: 700; margin-bottom: 0.5rem; background: linear-gradient(135deg, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    header p { color: var(--text-muted); font-size: 0.95rem; }
    .card { background-color: var(--card-bg); border: 1px solid var(--border); border-radius: 0.75rem; padding: 1.75rem; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3); }
    .form-group { margin-bottom: 1.25rem; }
    label { display: block; font-size: 0.875rem; font-weight: 500; margin-bottom: 0.5rem; color: var(--text); }
    input[type="text"] { width: 100%; padding: 0.75rem 1rem; border-radius: 0.5rem; border: 1px solid var(--border); background-color: #0f172a; color: var(--text); font-size: 0.95rem; }
    input[type="text"]:focus { outline: none; border-color: var(--accent); }
    .checkbox-group { display: flex; flex-direction: column; gap: 0.75rem; margin-bottom: 1.25rem; }
    .checkbox-item { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; color: var(--text-muted); cursor: pointer; }
    .checkbox-item input { accent-color: var(--accent); width: 1rem; height: 1rem; cursor: pointer; }
    .session-details { margin-bottom: 1.5rem; border: 1px solid var(--border); border-radius: 0.5rem; padding: 0.75rem 1rem; background-color: #0f172a; }
    .session-details summary { cursor: pointer; font-size: 0.875rem; font-weight: 600; color: var(--accent); outline: none; }
    .session-body { margin-top: 0.85rem; }
    .help-text { font-size: 0.82rem; color: var(--text-muted); margin-bottom: 0.85rem; line-height: 1.5; }
    .code-badge { background: #1e293b; color: #38bdf8; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 0.85rem; }
    .btn-row { display: flex; gap: 1rem; }
    button { flex: 1; padding: 0.75rem 1rem; border-radius: 0.5rem; border: none; font-weight: 600; font-size: 0.95rem; cursor: pointer; transition: all 0.15s ease-in-out; }
    .btn-primary { background-color: var(--accent); color: #0f172a; }
    .btn-primary:hover { background-color: var(--accent-hover); }
    .btn-secondary { background-color: #334155; color: var(--text); }
    .btn-secondary:hover { background-color: #475569; }
    #status-area { margin-top: 1.5rem; display: none; }
    .alert { padding: 1rem; border-radius: 0.5rem; font-size: 0.9rem; margin-bottom: 1rem; line-height: 1.5; word-break: break-word; }
    .alert-danger { background-color: rgba(239, 68, 68, 0.15); border: 1px solid var(--danger); color: #fca5a5; }
    .alert-warning { background-color: rgba(245, 158, 11, 0.15); border: 1px solid var(--warning); color: #fde68a; }
    .stats { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; font-size: 0.875rem; color: var(--text-muted); }
    .stats span.badge { background: #334155; padding: 0.25rem 0.6rem; border-radius: 0.375rem; color: var(--accent); font-weight: 600; }
    textarea { width: 100%; height: 220px; background-color: #0f172a; border: 1px solid var(--border); border-radius: 0.5rem; color: var(--text); padding: 0.75rem; font-family: monospace; font-size: 0.875rem; resize: vertical; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Instagram Comment Extractor</h1>
      <p>Paginates all comments & nested replies, preserving exact duplicate order.</p>
    </header>
    <div class="card">
      <form id="extract-form" action="/api/download" method="POST">
        <div class="form-group">
          <label for="url">Instagram Post / Reel / TV URL</label>
          <input type="text" id="url" name="url" placeholder="https://www.instagram.com/p/..." required>
        </div>
        <div class="checkbox-group">
          <label class="checkbox-item">
            <input type="checkbox" id="include_replies" name="include_replies" checked>
            Include nested comment replies
          </label>
          <label class="checkbox-item">
            <input type="checkbox" id="include_owner" name="include_owner">
            Include comments authored by post owner
          </label>
        </div>

        <details class="session-details">
          <summary>Session Authentication (Required by Instagram for Comments)</summary>
          <div class="session-body">
            <p class="help-text">
              Instagram strictly requires an active login session to paginate comments.<br>
              1. Run <code class="code-badge">instaloader -l &lt;username&gt;</code> in your terminal once to log in.<br>
              2. Enter your username below to use that saved session:
            </p>
            <div class="form-group">
              <label for="session_user" style="font-size: 0.8rem;">Session Username</label>
              <input type="text" id="session_user" name="session_user" placeholder="e.g. teoo.mnl" value="teoo.mnl">
            </div>
            <div class="form-group" style="margin-bottom: 0;">
              <label for="session_file" style="font-size: 0.8rem;">Custom Session File Path (Optional)</label>
              <input type="text" id="session_file" name="session_file" placeholder="Leave empty for default session path">
            </div>
          </div>
        </details>

        <div class="btn-row">
          <button type="button" id="preview-btn" class="btn-secondary">Preview Extracted</button>
          <button type="submit" id="download-btn" class="btn-primary">Download .txt File</button>
        </div>
      </form>

      <div id="status-area">
        <div id="error-box" class="alert alert-danger" style="display: none;"></div>
        <div id="warning-box" class="alert alert-warning" style="display: none;"></div>
        <div id="results-box" style="display: none;">
          <div class="stats">
            <span>Shortcode: <b id="shortcode-val">-</b></span>
            <span>Total Usernames: <span id="count-val" class="badge">0</span></span>
          </div>
          <textarea id="preview-textarea" readonly></textarea>
        </div>
      </div>
    </div>
  </div>

  <script>
    const previewBtn = document.getElementById('preview-btn');
    const downloadBtn = document.getElementById('download-btn');
    const extractForm = document.getElementById('extract-form');
    const statusArea = document.getElementById('status-area');
    const errorBox = document.getElementById('error-box');
    const warningBox = document.getElementById('warning-box');
    const resultsBox = document.getElementById('results-box');
    const shortcodeVal = document.getElementById('shortcode-val');
    const countVal = document.getElementById('count-val');
    const previewTextarea = document.getElementById('preview-textarea');

    function getPayload() {
      const url = document.getElementById('url').value.trim();
      const includeOwner = document.getElementById('include_owner').checked;
      const includeReplies = document.getElementById('include_replies').checked;
      const sessionUser = document.getElementById('session_user').value.trim();
      const sessionFile = document.getElementById('session_file').value.trim();
      return {
        url: url,
        include_owner: includeOwner,
        include_replies: includeReplies,
        session_user: sessionUser || null,
        session_file: sessionFile || null,
      };
    }

    previewBtn.addEventListener('click', async () => {
      const payload = getPayload();
      if (!payload.url) {
        alert('Please enter an Instagram URL.');
        return;
      }

      statusArea.style.display = 'block';
      errorBox.style.display = 'none';
      warningBox.style.display = 'none';
      resultsBox.style.display = 'none';
      previewBtn.textContent = 'Extracting...';
      previewBtn.disabled = true;

      try {
        const res = await fetch('/api/extract', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        const contentType = res.headers.get('content-type') || '';
        let data = {};
        if (contentType.includes('application/json')) {
          data = await res.json();
        } else {
          const rawText = await res.text();
          data = { detail: rawText || `HTTP ${res.status}: ${res.statusText}` };
        }

        if (!res.ok) {
          throw new Error(data.detail || 'Extraction failed');
        }

        shortcodeVal.textContent = data.shortcode;
        countVal.textContent = data.count;
        previewTextarea.value = (data.usernames || []).join('\\n');
        resultsBox.style.display = 'block';

        if (data.is_partial && data.warning_message) {
          warningBox.textContent = data.warning_message;
          warningBox.style.display = 'block';
        }
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.style.display = 'block';
      } finally {
        previewBtn.textContent = 'Preview Extracted';
        previewBtn.disabled = false;
      }
    });

    extractForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const payload = getPayload();
      if (!payload.url) return;

      statusArea.style.display = 'block';
      errorBox.style.display = 'none';
      warningBox.style.display = 'none';
      downloadBtn.textContent = 'Downloading...';
      downloadBtn.disabled = true;

      try {
        const res = await fetch('/api/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        if (!res.ok) {
          const contentType = res.headers.get('content-type') || '';
          let errMsg = `Download failed (HTTP ${res.status})`;
          if (contentType.includes('application/json')) {
            const errData = await res.json();
            errMsg = errData.detail || errMsg;
          } else {
            const text = await res.text();
            errMsg = text || errMsg;
          }
          throw new Error(errMsg);
        }

        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        let filename = 'usernames.txt';
        const match = disposition.match(/filename="?([^"]+)"?/);
        if (match && match[1]) filename = match[1];

        const blobUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(blobUrl);
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.style.display = 'block';
      } finally {
        downloadBtn.textContent = 'Download .txt File';
        downloadBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


async def _parse_request_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        data = await request.json()
        return {
            "url": data.get("url", ""),
            "include_owner": bool(data.get("include_owner", False)),
            "include_replies": bool(data.get("include_replies", True)),
            "session_user": data.get("session_user"),
            "session_file": data.get("session_file"),
        }

    body = await request.body()
    parsed_qs = parse_qs(body.decode("utf-8"))

    url = parsed_qs.get("url", [""])[0]
    include_owner = parsed_qs.get("include_owner", ["false"])[0] in ("true", "True", "on", "1", True)
    include_replies = parsed_qs.get("include_replies", ["true"])[0] in ("true", "True", "on", "1", True)
    session_user = parsed_qs.get("session_user", [None])[0]
    session_file = parsed_qs.get("session_file", [None])[0]

    return {
        "url": url,
        "include_owner": include_owner,
        "include_replies": include_replies,
        "session_user": session_user,
        "session_file": session_file,
    }


def create_app(provider: CommentProvider | None = None) -> FastAPI:
    """Factory function creating the FastAPI app with optional injected CommentProvider."""
    app = FastAPI(title="Instagram Comment Extractor")

    def _get_provider_and_result(payload: dict[str, Any]):
        raw_url = payload["url"]
        try:
            parsed = parse_instagram_url(raw_url)
        except InvalidInstagramURLError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        active_provider = provider
        if active_provider is None:
            session_user = payload.get("session_user") or None
            session_file = Path(payload["session_file"]) if payload.get("session_file") else None

            if not session_user and not session_file:
                try:
                    import instaloader.instaloader as il_mod
                    s_dir = Path(il_mod.get_default_session_filename("dummy")).parent
                    s_files = list(s_dir.glob("session-*"))
                    if s_files:
                        session_user = s_files[0].name.replace("session-", "")
                except Exception:
                    pass

            try:
                active_provider = InstaloaderCommentProvider(
                    session_user=session_user,
                    session_file=session_file,
                )
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Session file not found: {exc}",
                ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to initialize session: {exc}",
                ) from exc

        try:
            result = extract_usernames(
                shortcode=parsed.shortcode,
                provider=active_provider,
                include_owner=payload["include_owner"],
                include_replies=payload["include_replies"],
            )
        except LoginRequiredException as exc:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Instagram requires an authenticated session to access comments. "
                    "Please enter your username under 'Session Authentication'. "
                    "Run 'instaloader -l <username>' in your terminal to save a session."
                ),
            ) from exc
        except RateLimitExceededError as exc:
            raise HTTPException(
                status_code=429,
                detail=f"Instagram rate limit exceeded: {exc}",
            ) from exc
        except InstaloaderException as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Instagram error: {exc}",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Extraction error: {exc}",
            ) from exc

        return parsed, result

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(content=HTML_TEMPLATE, status_code=200)

    @app.post("/api/extract")
    async def extract_api(request: Request) -> dict[str, Any]:
        payload = await _parse_request_payload(request)
        parsed, result = _get_provider_and_result(payload)
        write_usernames_file(result.shortcode, result.usernames, DEFAULT_OUTPUT_DIR)

        return {
            "shortcode": result.shortcode,
            "canonical_url": parsed.canonical_url,
            "count": len(result.usernames),
            "is_partial": result.is_partial,
            "warning_message": result.warning_message,
            "usernames": result.usernames,
        }

    @app.post("/api/download")
    async def download_api(request: Request) -> Response:
        payload = await _parse_request_payload(request)
        _, result = _get_provider_and_result(payload)

        write_usernames_file(result.shortcode, result.usernames, DEFAULT_OUTPUT_DIR)
        formatted_payload = format_usernames_payload(result.usernames)

        return Response(
            content=formatted_payload,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{result.shortcode}_usernames.txt"'
            },
        )

    return app


# Default app instance for running with `uvicorn src.web:app --reload`
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.web:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        app_dir=str(_PROJECT_ROOT),
    )
