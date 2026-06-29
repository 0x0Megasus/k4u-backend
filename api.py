import socket
import requests
import base64
import time
import json

class YacineTV:

  _HOST = "ver3.yacinelive.com"
  key = "c!xZj+N9&G@Ev@vw"

  # Cloudflare IPs for ver3.yacinelive.com — system DNS on Railway
  # sometimes resolves to the origin nginx directly (404). Hardcoding
  # Cloudflare IPs ensures we always go through the CDN.
  _CF_IPS = ["172.67.203.73", "104.21.37.24"]

  _HEADERS = {
    "User-Agent": (
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Referer": "https://x.com/",
  }

  def __init__(self):
    # Resolve the hostname via system DNS as a first guess
    try:
      self._ip = socket.getaddrinfo(self._HOST, 80, socket.AF_INET)[0][4][0]
    except Exception:
      self._ip = self._CF_IPS[0]
    # Verify the IP works; fall back to known Cloudflare IPs
    self._ip = self._resolve_working_ip()

  def _resolve_working_ip(self):
    """Try system-DNS IP first, then known Cloudflare IPs, return the first that works."""
    candidates = [self._CF_IPS[0], self._CF_IPS[1]]
    if self._ip not in candidates:
      candidates.insert(0, self._ip)
    tried = set()
    for ip in candidates:
      if ip in tried:
        continue
      tried.add(ip)
      try:
        r = requests.get(
          f"http://{ip}/api/events",
          headers={**self._HEADERS, "Host": self._HOST},
          timeout=5,
        )
        if r.status_code == 200 and "T" in r.headers:
          return ip
      except Exception:
        continue
    # All failed — return the first CF IP anyway, better than nothing
    return self._CF_IPS[0]

  def decrypt(self, enc, key=key):
    enc = base64.b64decode(enc.encode("ascii")).decode("ascii")
    result = ""
    for i in range(len(enc)):
      result = result + chr(ord(enc[i]) ^ ord(key[i % len(key)]))
    return result

  def req(self, path):
    headers = {**self._HEADERS, "Host": self._HOST}
    r = requests.get(f"http://{self._ip}{path}", headers=headers, timeout=15)
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
