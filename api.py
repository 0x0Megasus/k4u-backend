import socket
import ssl
import http.client
import base64
import time
import json

class YacineTV:

  _HOST = "ver3.yacinelive.com"
  key = "c!xZj+N9&G@Ev@vw"

  # Cloudflare IPs for ver3.yacinelive.com — Railway's network can't reach the
  # upstream through Cloudflare via regular HTTP (transparent proxy or DNS
  # bypasses Cloudflare, hitting the nginx origin directly which returns 404).
  # By connecting via HTTPS with SNI set to the real hostname, we bypass any
  # transparent HTTP proxy AND ensure Cloudflare terminates the TLS.
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
    # Build a custom SSL context: connect to the CF IP but send SNI for _HOST.
    # This lets Cloudflare terminate TLS correctly.
    self._ctx = ssl.create_default_context()
    self._ctx.check_hostname = False
    self._ctx.verify_mode = ssl.CERT_NONE

    # Pick the first responsive CF IP
    self._ip = self._resolve_cf_ip()

  def _resolve_cf_ip(self):
    """Probe known Cloudflare IPs, return the first that serves the API."""
    for ip in self._CF_IPS:
      try:
        resp, body = self._do_request(ip, "/api/events", timeout=5)
        if resp.status == 200 and resp.getheader("T"):
          return ip
      except Exception:
        continue
    # Fallback to the first IP
    return self._CF_IPS[0]

  def _do_request(self, ip, path, timeout=15):
    """Make an HTTPS request to *ip* with SNI for _HOST, returning (response, body_text)."""
    raw = socket.create_connection((ip, 443), timeout=timeout)
    ssl_sock = self._ctx.wrap_socket(raw, server_hostname=self._HOST)
    conn = http.client.HTTPSConnection(self._HOST, context=self._ctx)
    conn.sock = ssl_sock
    conn.request("GET", path, headers={**self._HEADERS, "Host": self._HOST})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", errors="replace")
    return resp, body

  def decrypt(self, enc, key=key):
    enc = base64.b64decode(enc.encode("ascii")).decode("ascii")
    result = ""
    for i in range(len(enc)):
      result = result + chr(ord(enc[i]) ^ ord(key[i % len(key)]))
    return result

  def req(self, path):
    resp, body = self._do_request(self._ip, path, timeout=15)
    timestamp = str(int(time.time()))
    if resp.getheader("t"):
      timestamp = resp.getheader("t")

    try:
      return json.loads(self.decrypt(body, key=self.key + timestamp))

    except Exception:
      snippet = (body[:300] if body else "empty") + (
        "…" if body and len(body) > 300 else ""
      )
      print(
        f"[YacineTV] req({path}) failed — "
        f"HTTP {resp.status}, "
        f"Content-Type: {resp.getheader('Content-Type', '?')}, "
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
