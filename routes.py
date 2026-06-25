from fastapi import APIRouter
from api import YacineTV
from stream_token_store import stream_tokens

ytv = YacineTV()
router = APIRouter(tags=["YacineTV"])


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


def _tokenize_sources(data: list) -> list:
    """Replace raw URLs with short-lived tokens so the frontend never sees upstream CDN URLs."""
    tokenized = []
    for src in data:
        if not isinstance(src, dict):
            tokenized.append(src)
            continue
        url = src.get("url")
        if url:
            token = stream_tokens.create_token(
                url=url,
                user_agent=src.get("user_agent"),
                referer=src.get("referer"),
                headers=src.get("headers"),
            )
            tokenized.append({
                "name": src.get("name"),
                "token": token,
            })
        else:
            # No URL — pass through as-is (shouldn't happen)
            tokenized.append(src)
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
