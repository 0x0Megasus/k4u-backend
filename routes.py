import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter
from api import YacineTV
from stream_token_store import stream_tokens

ytv = YacineTV()
router = APIRouter(tags=["YacineTV"])

_REDIRECTOR_TIMEOUT = 10  # seconds for redirector page fetch


def wrap(result):
    """Normalize upstream response to always have a 'success' field."""
    if isinstance(result, dict):
        # Upstream error format
        if result.get("success") is False:
            return result
        # Upstream success format: {"vt":0,"data":[...]} or {"data":[...]}
        if "data" in result:
            return {"success": True, "data": result["data"]}
    # Fallback: wrap whatever we got
    return {"success": True, "data": result}


def _is_redirector_url(url: str) -> bool:
    """Check if a URL looks like an HTML redirector page, not a direct stream."""
    if not url:
        return False
    lower = url.lower()
    # HTML pages
    if ".html" in lower or ".php" in lower:
        return True
    # Known redirector patterns (mbch.live)
    if "mbch.live" in lower and "/pl/" in lower:
        return True
    return False


def _resolve_redirector(url: str, user_agent: str | None,
                        referer: str | None, headers: dict | None) -> str:
    """Try to resolve an HTML redirector page to the actual M3U8 stream URL.

    Some MBC channels return HTML player pages instead of direct M3U8 URLs.
    Many of these pages embed the real M3U8 URL in a <script> or plain text.
    This function fetches the page and looks for the embedded URL.

    Returns the original URL if no M3U8 URL can be found.
    """
    fetch_headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        ),
        "Referer": referer or "https://x.com/",
    }
    if headers and isinstance(headers, dict):
        fetch_headers.update(headers)

    try:
        r = requests.get(url, headers=fetch_headers, timeout=_REDIRECTOR_TIMEOUT)
        if r.status_code != 200:
            return url

        # Skip binary / non-HTML responses
        content_type = r.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return url

        text = r.text

        # Look for M3U8 URLs in the HTML
        # Pattern 1: Direct URL in attribute or text
        m3u8_urls = re.findall(
            r'https?://[^"\'<>\s]+\.m3u8[^"\'<>\s]*',
            text,
        )
        # Pattern 2: URLs with 'index.m3u8' that might not match above
        if not m3u8_urls:
            m3u8_urls = re.findall(
                r'https?://[^"\'<>\s]*/index\.m3u8[^"\'<>\s]*',
                text,
            )

        if m3u8_urls:
            resolved = m3u8_urls[0]
            # Sanitize against latin-1 issues
            resolved = resolved.encode("latin-1", errors="replace").decode("latin-1")
            return resolved

    except requests.RequestException:
        pass

    return url


def _sanitize_header_value(value: str | None) -> str | None:
    """Clean header values to be latin-1 safe.

    The upstream API sometimes returns garbage User-Agent fields (all U+3164
    hangul filler characters) — treat those as None so the default is used.
    """
    if not value:
        return None
    # If every printable char is non-ASCII, it's garbage -> use default
    printable = [c for c in value if c.isprintable()]
    if printable and all(ord(c) > 127 for c in printable):
        return None
    return value.encode("latin-1", errors="replace").decode("latin-1")


_VALIDATE_TIMEOUT = 5  # seconds — quick HEAD to check if URL is alive


def _validate_source(src: dict) -> dict | None:
    """Check if a stream source URL is reachable.

    Does a quick HEAD request with a short timeout. Returns the source dict
    if valid, or None if the URL is dead (timeout, 4xx, 5xx).
    """
    url = src.get("url")
    if not url:
        return src  # No URL — pass through (shouldn't happen)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0.0.0 Safari/537.36"
        ),
    }
    ua = _sanitize_header_value(src.get("user_agent"))
    ref = _sanitize_header_value(src.get("referer"))
    if ua:
        headers["User-Agent"] = ua
    if ref:
        headers["Referer"] = ref
    if src.get("headers") and isinstance(src.get("headers"), dict):
        headers.update(src["headers"])

    try:
        # HEAD first — fast, no body downloaded
        r = requests.head(url, headers=headers, timeout=_VALIDATE_TIMEOUT,
                          allow_redirects=True)
        if r.status_code < 400:
            return src
        # HEAD failed — try GET (some CDN nodes reject HEAD)
        r = requests.get(url, headers=headers, timeout=_VALIDATE_TIMEOUT,
                         stream=True)
        r.close()
        if r.status_code < 400:
            return src
        print(f"[ROUTES] Source dead — url={url} status={r.status_code}")
        return None
    except requests.RequestException as e:
        print(f"[ROUTES] Source unreachable — url={url} error={e}")
        return None


def _tokenize_sources(data: list) -> list:
    """Replace raw URLs with short-lived tokens so the frontend never sees upstream CDN URLs.

    1. Resolve HTML redirector pages to direct M3U8 URLs
    2. Validate all URLs in parallel (HEAD/GET with short timeout)
    3. Create tokens only for sources that are reachable
    """
    # Step 1: Resolve redirectors (sequential — usually fast)
    resolved_sources = []
    for src in data:
        if not isinstance(src, dict):
            resolved_sources.append(src)
            continue
        url = src.get("url")
        if not url:
            resolved_sources.append(src)
            continue

        if _is_redirector_url(url):
            new_url = _resolve_redirector(
                url,
                user_agent=src.get("user_agent"),
                referer=src.get("referer"),
                headers=src.get("headers"),
            )
            if new_url != url:
                src = {**src, "url": new_url,
                       "user_agent": None, "referer": None, "headers": None}

        resolved_sources.append(src)

    # Step 2: Validate all URLs in parallel
    valid_sources = []
    with ThreadPoolExecutor(max_workers=min(len(resolved_sources), 5)) as pool:
        futures = {
            pool.submit(_validate_source, src): src
            for src in resolved_sources
            if isinstance(src, dict) and src.get("url")
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                valid_sources.append(result)

    # Re-add non-dict entries (shouldn't happen but be safe)
    for src in resolved_sources:
        if not isinstance(src, dict):
            valid_sources.append(src)

    # Step3: Tokenize only valid sources
    tokenized = []
    for src in valid_sources:
        if not isinstance(src, dict) or not src.get("url"):
            continue
        ua = _sanitize_header_value(src.get("user_agent"))
        ref = _sanitize_header_value(src.get("referer"))
        extra_headers = src.get("headers")
        token = stream_tokens.create_token(
            url=src["url"],
            user_agent=ua,
            referer=ref,
            headers=extra_headers,
        )
        tokenized.append({
            "name": src.get("name"),
            "token": token,
        })

    skipped = len(data) - len(tokenized)
    if skipped > 0:
        print(f"[ROUTES] Pre-validation skipped {skipped}/{len(data)} dead sources")
    return tokenized


@router.get("/categories")
def categories():
    return wrap(ytv.get_categories())


@router.get("/categories/{category_id}/channels")
def channels(category_id: int):
    return wrap(ytv.get_category_channels(category_id))


@router.get("/channel/{channel_id}")
def channel(channel_id: int):
    result = wrap(ytv.get_channel(channel_id))
    if result.get("success") and isinstance(result.get("data"), list):
        result["data"] = _tokenize_sources(result["data"])
    return result


@router.get("/events")
def events():
    return wrap(ytv.req("/api/events"))


@router.get("/event/{event_id}")
def event_streams(event_id: int):
    result = wrap(ytv.req(f"/api/event/{event_id}"))
    if result.get("success") and isinstance(result.get("data"), list):
        result["data"] = _tokenize_sources(result["data"])
    return result
