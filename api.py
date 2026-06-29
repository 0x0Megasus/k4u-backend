import requests
import base64
import time
import json

class YacineTV:

  api_url = "https://ver3.yacinelive.com"
  key = "c!xZj+N9&G@Ev@vw"

  _HEADERS = {
    "User-Agent": (
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Referer": "https://x.com/",
  }

  def __init__(self):
    pass

  def decrypt(self, enc, key=key):
    enc = base64.b64decode(enc.encode("ascii")).decode("ascii")
    result = ""
    for i in range(len(enc)):
      result = result + chr(ord(enc[i]) ^ ord(key[i % len(key)]))
    return result

  def req(self, path):
    r = requests.get(self.api_url + path, headers=self._HEADERS, timeout=15)
    timestamp = str(int(time.time()))
    if "t" in r.headers:
      timestamp = r.headers["t"]

    try:
      return json.loads(self.decrypt(r.text, key=self.key + timestamp))

    except Exception:
      # Log the upstream response snippet for debugging
      snippet = (r.text[:300] if r.text else "empty") + (
        "…" if r.text and len(r.text) > 300 else ""
      )
      print(
        f"[YacineTV] req({path}) failed — "
        f"HTTP {r.status_code}, "
        f"Content-Type: {r.headers.get('Content-Type', '?')}, "
        f"snippet: {snippet}"
      )
      return {
        "success": False,
        "error": "can't parse json."
      }

  def get_categories(self):
    return self.req("/api/categories")

  def get_category_channels(self, category_id):
    return self.req(f"/api/categories/{str(category_id)}/channels")

  def get_channel(self, channel_id):
    return self.req(f"/api/channel/{str(channel_id)}")
