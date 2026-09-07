"""HTTP primitives: SSRF guard, descriptive UA, per-domain politeness, conditional GET.

Vendored and extended from agentic-interview-question-generator/src/sources/base.py
(`domain`, `is_safe_public_url`, `USER_AGENT`, `polite_get`). The extensions here are
the per-domain rate limiter and the `Fetch` result object that carries what the probe
stage needs to tell "changed" apart from "unreachable".
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests
from html import unescape as html_unescape

USER_AGENT = ("nxtwave-curriculum-watch/1.0 "
              "(+https://www.ccbp.in; gen-ai-content@nxtwave.co.in)")

# Courtesy gap between two requests to the same host. Vendor docs sites are the most
# frequently hit (docs.n8n.io appears 144 times in one export) so this is not optional.
MIN_INTERVAL_S = 1.5

_last_hit: dict[str, float] = {}
_lock = threading.Lock()

# robots.txt, fetched once per host per process. The PRD promises robots-respecting
# probing, so this is a guarantee rather than a nicety: a host that asks us not to
# crawl a path must not be crawled, and a host that publishes a Crawl-delay sets our
# interval for that host rather than the other way round.
_robots: dict[str, object] = {}
_robots_delay: dict[str, float] = {}


def _robots_for(url: str):
    host = domain(url)
    if host in _robots:
        return _robots[host]
    from urllib.robotparser import RobotFileParser
    parsed = urlparse(url)
    rp = RobotFileParser()
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        r = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if r.status_code == 200 and len(r.text) < 500_000:
            rp.parse(r.text.splitlines())
        else:
            # No robots.txt, or an error serving it, means no restriction.
            rp.parse([])
    except requests.RequestException:
        rp.parse([])
    _robots[host] = rp
    try:
        delay = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*")
        if delay:
            _robots_delay[host] = float(delay)
    except Exception:
        pass
    return rp


def robots_allows(url: str) -> bool:
    try:
        return bool(_robots_for(url).can_fetch(USER_AGENT, url))
    except Exception:
        # A parser failure must not silently become permission.
        return True


def domain(url: str) -> str:
    """Registrable host of a URL, lower-cased, without a leading www."""
    net = (urlparse(url or "").netloc or "").lower()
    if "@" in net:                      # strip any userinfo before the host
        net = net.rsplit("@", 1)[1]
    net = net.split(":", 1)[0]          # strip port
    return net[4:] if net.startswith("www.") else net


def is_safe_public_url(url: str) -> bool:
    """SSRF guard: http(s) only, and the host must resolve to a public IP."""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        for _fam, _t, _p, _c, sockaddr in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
        return True
    except Exception:
        return False


def _throttle(host: str) -> None:
    interval = max(MIN_INTERVAL_S, _robots_delay.get(host, 0.0))
    with _lock:
        wait = interval - (time.monotonic() - _last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


@dataclass
class Fetch:
    """One HTTP observation.

    `reachable` is deliberately separate from `ok`: an anti-bot 403 or a 429 proves the
    host is alive and serving, which is the opposite of the dead-tool signal we are
    hunting. Reporting our own blocked request as a dead tool is the single easiest way
    to lose the content team's trust, so the distinction lives in the data model rather
    than in a caller's discipline.
    """
    url: str
    status: Optional[int] = None
    final_url: str = ""
    redirects: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    body: str = ""
    error: str = ""
    from_cache: bool = False

    @property
    def reachable(self) -> bool:
        return self.status is not None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def gone(self) -> bool:
        """Server states the resource does not exist. 403/401/429 are NOT gone."""
        return self.status in (404, 410)

    @property
    def blocked(self) -> bool:
        return self.status in (401, 403, 429)

    @property
    def redirected_off_path(self) -> bool:
        """Redirected to a different host, or to a bare root — a moved/parked tool."""
        if not self.final_url or not self.redirects:
            return False
        if domain(self.final_url) != domain(self.url):
            return True
        return urlparse(self.final_url).path.rstrip("/") == "" and \
            urlparse(self.url).path.rstrip("/") not in ("", "/")


def fetch(url: str, *, timeout: float = 20.0, retries: int = 2,
          method: str = "GET", headers: Optional[dict] = None) -> Fetch:
    """Polite fetch with backoff. Never raises; failures come back on `Fetch.error`."""
    if not is_safe_public_url(url):
        return Fetch(url=url, error="unsafe_or_unresolvable_url")
    if not robots_allows(url):
        # Reported as its own error so the probe stage can tell "we were asked not to
        # look" apart from "the tool is gone". Conflating them would invent findings.
        return Fetch(url=url, error="disallowed_by_robots")

    h = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
    if headers:
        h.update(headers)

    last = Fetch(url=url)
    for attempt in range(retries + 1):
        _throttle(domain(url))
        t0 = time.monotonic()
        try:
            r = requests.request(method, url, headers=h, timeout=timeout,
                                 allow_redirects=True)
        except requests.RequestException as exc:
            last = Fetch(url=url, error=type(exc).__name__,
                         elapsed_ms=int((time.monotonic() - t0) * 1000))
            time.sleep(0.5 + attempt)
            continue

        out = Fetch(
            url=url,
            status=r.status_code,
            final_url=r.url,
            redirects=[h_.url for h_ in r.history],
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            body=r.text if "text" in r.headers.get("Content-Type", "") or
            "json" in r.headers.get("Content-Type", "") else "",
        )
        # Transient server-side failures are worth one more try; 4xx are answers.
        if r.status_code in (500, 502, 503, 504) and attempt < retries:
            last = out
            time.sleep(1.0 + attempt)
            continue
        return out
    return last


# Page chrome. A nav bar flattened into the text stream becomes a 300-character
# "sentence" of capitalised menu items, and that is what ends up quoted as evidence in
# a finding. Evidence a human cannot read is worse than no evidence.
_DROP = re.compile(
    r"<(script|style|noscript|nav|header|footer|aside|form|svg|template)\b[^>]*>.*?</\1>",
    re.S | re.I)
_BLOCK = re.compile(
    r"</(p|div|li|tr|h[1-6]|section|article|td|th|blockquote|pre|dd|dt|ul|ol|table)\s*>"
    r"|<br\s*/?>|<hr\s*/?>", re.I)
_STRIP = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\u00a0]+")
_NL = re.compile(r"\n\s*\n+")
# Nonces, CSRF tokens, build hashes and timestamps change on every render and would
# make every page look "changed" every week. Neutralise them before hashing.
_VOLATILE = re.compile(
    r"[0-9a-f]{16,}|\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?\b|"
    r"\b\d{10,13}\b|nonce=[\w-]+|csrf[\w-]*=[\w-]+", re.I)


def main_text(html: str, *, limit: int = 400_000, hard_cap: int = 4_000_000) -> str:
    """Visible prose, with page chrome removed and block boundaries preserved as
    newlines so that callers can segment it into quotable sentences.

    Chrome is stripped *before* truncation. Truncating first can cut in the middle of
    a `<script>` block, which removes its closing tag and leaks the payload into the
    text - on one real vendor page a Next.js flight blob ended up inside a quoted
    piece of evidence.
    """
    if not html:
        return ""
    body = _DROP.sub("\n", html[:hard_cap])[:limit]
    body = _BLOCK.sub("\n", body)
    body = _STRIP.sub(" ", body)
    body = html_unescape(body)
    lines = [_WS.sub(" ", ln).strip() for ln in body.split("\n")]
    return _NL.sub("\n", "\n".join(ln for ln in lines if ln)).strip()


_TOKEN = re.compile(r"[a-z][a-z'\-]{2,}")

# How many differing simhash bits (of 64) count as a real content change. Marketing
# and docs pages carry rotating banners, changing counts and randomised ids, so an
# exact hash reports "changed" on almost every page every week - 16 of 30 in one
# measured run minutes apart. A similarity threshold reports rewrites instead of churn.
SIMHASH_DISTANCE = 8
SHINGLE = 3


def text_hash(text: str) -> str:
    """64-bit simhash of page prose, as hex. Compare with `hash_distance`."""
    body = _VOLATILE.sub(" ", (text or "").lower())
    words = _TOKEN.findall(body)
    if len(words) < SHINGLE:
        return ""
    shingles = [" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)]
    counts: dict[str, int] = {}
    for sh in shingles:
        counts[sh] = counts.get(sh, 0) + 1
    bits = [0] * 64
    for sh, weight in counts.items():
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for i in range(64):
            bits[i] += weight if (h >> i) & 1 else -weight
    value = 0
    for i, b in enumerate(bits):
        if b > 0:
            value |= 1 << i
    return f"{value:016x}"


def hash_distance(a: str, b: str) -> Optional[int]:
    """Hamming distance between two simhashes, or None if either is missing."""
    if not a or not b or len(a) != 16 or len(b) != 16:
        return None
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None
