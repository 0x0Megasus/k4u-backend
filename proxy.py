import re
import requests
import urllib3
from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from urllib.parse import urljoin

from stream_token_store import stream_tokens

router = APIRouter(prefix="/api/proxy", tags=["Proxy"])

CHUNK_SIZE = 1024 * 1024  # 1MB

# Retry strategy for the flaky upstream CDN:
#   - Retry on 502/503/504 (CDN node saturated or temporarily unreachable)
#   - Also retry on connection errors (DNS, TCP, TLS failures)
#   - Exponential backoff: 0.3s → 0.6s → 1.2s → 2.4s → 4.8s (capped at 10s)
#   - Do not retry on 200, 404, etc. — those are final.
# Without this, the default urllib3 Retry only covers connection-level errors,
# so a single 502 from a saturated CDN node immediately fails the whole request
# even though a retry to a different node would succeed.
_retry = urllib3.Retry(
    total=5,                     # Max 5 attempts total (1 original + 4 retries)
    connect=3,                   # Retry up to 3 times on connection errors
    read=2,                      # Retry up to 2 times on incomplete reads
    status=4,                    # Retry up to 4 times on retryable status codes
    other=1,                     # Retry once on other errors
    backoff_factor=0.3,          # Sleep 0.3s * (2^(retry_num - 1)) between retries
    status_forcelist={502, 503, 504},  # Only retry on these HTTP status codes
    allowed_methods={"GET"},     # Only retry GET requests (safe to repeat)
    raise_on_status=False,       # Return the last response instead of raising
)

# Shared session with connection pooling — reuses TCP/TLS connections to
# the upstream CDN across segment requests, reducing per-segment latency.
# Without this, every segment creates a new connection (TCP + TLS handshake).
_http = requests.Session()
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=20,   # Keep 20 connections to the CDN
    pool_maxsize=100,      # Max 100 connections total
    max_retries=_retry,    # Custom retry strategy (handles 502/503/504 too)
    pool_block=False,      # Don't block when pool is exhausted
)
_http.mount("https://", _adapter)
_http.mount("http://", _adapter)


def _sanitize_header(value: str) -> str:
    """Strip characters that can't be encoded as latin-1 (HTTP header requirement).

    The upstream API sometimes returns header values containing non-latin-1
    characters (e.g. \u3164) which cause UnicodeEncodeError in urllib3.
    """
    if not value:
        return value
    return value.encode("latin-1", errors="replace").decode("latin-1")


def _sanitize_headers(headers: dict) -> dict:
    """Apply latin-1 sanitization to all header values."""
    return {k: _sanitize_header(v) if isinstance(v, str) else v
            for k, v in headers.items()}


def _make_headers(user_agent: str | None = None, referer: str | None = None,
                  extra: dict | None = None) -> dict:
    """Build request headers for upstream CDN requests."""
    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        ),
        "Referer": referer or "https://x.com/",
    }
    if extra:
        headers.update(extra)
    return _sanitize_headers(headers)


def _rewrite_playlist(text: str, base_url: str, proxy_base: str) -> str:
    """Rewrite segment URLs inside an HLS playlist to go through our proxy."""

    def _replace(match):
        url = match.group(1)
        # Make absolute if relative
        if not url.startswith(("http://", "https://")):
            url = urljoin(base_url.rstrip("/") + "/", url)
        return proxy_base + requests.utils.quote(url, safe="")

    # Lines that are NOT comments/directives (i.e., they are URLs)
    pattern = re.compile(r"^(https?://\S+)$", re.MULTILINE)
    return pattern.sub(_replace, text)


def _fetch_and_proxy(url: str, headers: dict) -> Response:
    """Fetch an upstream URL and return a proxied Response (playlist or stream)."""
    try:
        upstream = _http.get(url, headers=headers, stream=True, timeout=30)
    except requests.RequestException as e:
        return Response(
            content=f'{{"error":"upstream request failed: {str(e)}"}}',
            status_code=502,
            media_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # If upstream still returns 5xx after all retries, bail with our JSON error
    if upstream.status_code in {502, 503, 504}:
        msg = 'upstream CDN temporarily unavailable (HTTP %d)' % upstream.status_code
        return Response(
            content='{"error":"%s"}' % msg,
            status_code=502,
            media_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    content_type = upstream.headers.get("Content-Type", "")
    is_playlist = ".m3u8" in url.lower() or "mpegurl" in content_type or "m3u8" in content_type

    # If upstream returned HTML instead of a stream, abort
    content_is_html = "text/html" in content_type
    if content_is_html:
        return Response(
            content='{"error":"upstream returned HTML instead of HLS playlist"}',
            status_code=502,
            media_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if is_playlist:
        text = upstream.text

        # Build the proxy base URL for rewriting segments
        # Use the URL-based proxy for segments (they change dynamically anyway)
        proxy_base = "/api/proxy/hls?url="
        base_url = url[: url.rfind("/") + 1] if "/" in url else url

        rewritten = _rewrite_playlist(text, base_url, proxy_base)

        return Response(
            content=rewritten,
            media_type=content_type or "application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "no-cache",
            },
        )
    else:
        return StreamingResponse(
            upstream.iter_content(chunk_size=CHUNK_SIZE),
            media_type=content_type or "application/octet-stream",
            status_code=upstream.status_code,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "no-cache",
            },
        )


# ── Token-based endpoint (secure) ──────────────────────────────────

@router.get("/stream/{token}")
async def proxy_stream_token(token: str):
    """Proxy an HLS stream by token — the upstream URL stays server-side.

    The token is a short-lived random string created by routes.py when the
    frontend requests event/channel stream sources. The token maps to the
    real CDN URL + required headers server-side.
    """
    info = stream_tokens.get(token)
    if info is None:
        return Response(
            content='{"error":"invalid or expired stream token"}',
            status_code=404,
            media_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    headers = _make_headers(
        user_agent=info.user_agent,
        referer=info.referer,
        extra=info.headers,
    )
    return _fetch_and_proxy(info.url, headers)


@router.options("/stream/{token}")
async def proxy_stream_token_options(token: str):
    """Handle CORS preflight for token-based proxy."""
    return Response(
        content="",
        media_type="text/plain",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400",
        },
    )


# ── URL-based endpoint (legacy/fallback) ───────────────────────────

@router.get("/hls")
async def proxy_hls(url: str = Query(...)):
    """Proxy HLS playlists and segments by URL (used for segment rewriting).

    This is primarily used for individual segment files that were rewritten
    in playlists. The token-based endpoint (/stream/{token}) is the secure
    entry point for initial stream sources.
    """
    headers = _make_headers()
    return _fetch_and_proxy(url, headers)


@router.options("/hls")
async def proxy_hls_options():
    """Handle CORS preflight."""
    return Response(
        content="",
        media_type="text/plain",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400",
        },
    )
