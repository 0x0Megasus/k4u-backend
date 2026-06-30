import re
import requests
from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from urllib.parse import urljoin

from stream_token_store import stream_tokens

router = APIRouter(prefix="/api/proxy", tags=["Proxy"])

CHUNK_SIZE = 1024 * 1024  # 1MB


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


def _rewrite_playlist(text: str, base_url: str) -> str:
    """Resolve segment URLs to absolute HTTPS CDN URLs (no proxy rewrite).

    Previously every segment URL was rewritten to go through Railway's proxy
    (/api/proxy/hls?url=...), adding ~200-500ms per segment — Railway is not
    a streaming CDN. Now segments are served directly from the upstream CDN,
    eliminating the proxy bottleneck.

    Relative URLs are resolved to absolute against the playlist base URL.
    All URLs are forced to HTTPS to avoid Mixed Content errors — the page
    is served over HTTPS (Vercel/Cloudflare) and browsers block HTTP XHR
    requests from HTTPS origins.
    """

    def _resolve(match):
        url = match.group(1)
        # Make absolute if relative
        if not url.startswith(("http://", "https://")):
            url = urljoin(base_url.rstrip("/") + "/", url)
        # Force HTTPS — upstream CDN URLs are often HTTP but CDNs
        # universally support HTTPS (Cloudflare, Akamai, etc.)
        if url.startswith("http://"):
            url = "https://" + url[7:]
        return url

    # Lines that are NOT comments/directives (i.e., they are URLs)
    pattern = re.compile(r"^(https?://\S+)$", re.MULTILINE)
    return pattern.sub(_resolve, text)


def _fetch_and_proxy(url: str, headers: dict) -> Response:
    """Fetch an upstream URL and return a proxied Response (playlist or stream)."""
    try:
        upstream = requests.get(url, headers=headers, stream=True, timeout=30)
    except requests.RequestException as e:
        return Response(
            content=f'{{"error":"upstream request failed: {str(e)}"}}',
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

        base_url = url[: url.rfind("/") + 1] if "/" in url else url

        rewritten = _rewrite_playlist(text, base_url)

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
