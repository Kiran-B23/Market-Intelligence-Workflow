"""Link extraction, and the derivation of official domains from the curriculum itself.

External tool links in these exports are `<a href="..." target="_blank">`, not
markdown — markdown `[](...)` and `![](...)` are used almost entirely for S3-hosted
media. All four syntaxes are handled here because all four occur in the same body
text, and prerequisite tool links cluster inside `<details>` blocks.

The important idea in this module is `registrable()`. When a content author writes
`<a href="https://console.groq.com/keys">`, that link *is* the statement of where
Groq's official ground truth lives. Deriving the authority set from the curriculum's
own links is far safer than recalling vendor domains from memory, and it means a tool
nobody has ever configured still gets a correct official domain on first sight.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from config.constants import INFRA_HOSTS, INTERNAL_HOSTS
from miw.net import domain

A_HREF = re.compile(r"""<a\s[^>]*?href\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s>]+))""", re.I)
IFRAME = re.compile(r"""<iframe\s[^>]*?src\s*=\s*(?:"([^"]+)"|'([^']+)')""", re.I)
IMG_SRC = re.compile(r"""<img\s[^>]*?src\s*=\s*(?:"([^"]+)"|'([^']+)')""", re.I)
MD_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?\s*(?:\"[^\"]*\")?\s*\)")
BARE = re.compile(r"(?<![\w\"'=(])https?://[^\s\)\]\}'\"<>\\`|]+")

_TRAIL = ".,;:!?)]}'\"*_"

# Multi-label public suffixes that appear in these courses. Not exhaustive by design:
# a wrong guess here only ever widens or narrows an authority set by one label, and
# every registry entry is reviewable.
_MULTI_SUFFIX = (
    "co.in", "co.uk", "com.au", "co.jp", "co.nz", "com.br", "co.za", "org.uk",
    "ac.in", "gov.in", "github.io", "gitlab.io", "readthedocs.io", "web.app",
    "firebaseapp.com", "vercel.app", "netlify.app", "streamlit.app", "hf.space",
    "pages.dev", "workers.dev", "herokuapp.com", "run.app", "azurewebsites.net",
)


def registrable(host: str) -> str:
    """Registrable domain of a host: the unit that identifies who owns it.

    `console.groq.com -> groq.com`, `foo.github.io -> foo.github.io` (each GitHub
    Pages site is its own owner, which is why the multi-label suffix list matters).
    """
    host = (host or "").lower().strip(".")
    if not host or host.replace(".", "").isdigit():
        return host
    for suf in _MULTI_SUFFIX:
        if host == suf:
            return host
        if host.endswith("." + suf):
            labels = host[: -(len(suf) + 1)].split(".")
            return f"{labels[-1]}.{suf}" if labels else host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def normalise_url(url: str) -> str:
    """Drop fragments and trailing punctuation swept up by the bare-URL regex."""
    url = (url or "").strip().rstrip(_TRAIL)
    if not url:
        return ""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""
    return urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))


def is_internal(url: str) -> bool:
    d = domain(url)
    return any(d == h or d.endswith("." + h) for h in INTERNAL_HOSTS)


def is_infra(url: str) -> bool:
    d = domain(url)
    return any(d == h or d.endswith("." + h) for h in INFRA_HOSTS)


MEDIA_EXT = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|bmp|tiff?|mp4|mp3|wav|pdf|zip|csv|ipynb|srt|vtt)$", re.I)

# News, press and reference domains. A link to a Reuters article in a reading material
# is a *citation*, not a tool the student operates - inventorying it as a dependency
# put "Bbc", "Nytimes" and "Teslarati" in the tool list and would have us monitoring
# newspapers for curriculum drift.
NEWS_PRESS = frozenset({
    "aibusiness.com", "apnews.com", "bbc.com", "bbc.co.uk", "cnbc.com", "fortune.com",
    "foxnews.com", "nytimes.com", "prnewswire.com", "teslarati.com", "time.com",
    "today.com", "axios.com", "newsbytesapp.com", "insurtechinsights.com",
    "kanerika.com", "thehackernews.com", "thestack.technology", "aclu.org",
    "googleblog.com", "blog.google", "nips.cc", "wildanimalinitiative.org",
    "normaltech.ai", "sandiegouniontribune.com", "earth.com", "thita.com",
    "feedzai.com", "flagright.com", "techsolutions.com", "zemith.com",
})

# Hostnames that are placeholders in example code, not real services.
_PLACEHOLDER = re.compile(
    r"(^|\.)(your|my|example|sample|test|placeholder|yourapp|myapp|company)[-.]|"
    r"^(your|my)[\w-]*\.", re.I)

# Docs-hosting platforms. Real content lives there, but each project's docs site is not
# a separate dependency from the project itself.
_DOCS_HOSTS = ("github.io", "readthedocs.io", "gitbook.io", "netlify.app",
               "githubusercontent.com", "pages.dev", "vercel.app", "onrender.com")


IMAGE_EXT = re.compile(r"\.(png|jpe?g|gif|webp|svg|bmp)(\?|$)", re.I)


def images_in(text: str) -> list[str]:
    """Image URLs in a body of text, from markdown and from `<img>`.

    Counted per unit rather than inventoried as dependencies. A course-hosted
    screenshot's own health is not the signal - what matters is that a session
    documenting a third-party flow with 34 screenshots has 34 things to redo when that
    flow changes, and a finding that says only "the docs moved" hides that entirely.
    """
    if not text or "http" not in text:
        return []
    out, seen = [], set()
    for rx in (MD_LINK, IMG_SRC):
        for m in rx.finditer(text):
            raw = next((g for g in m.groups() if g), "")
            u = normalise_url(raw)
            if u and u not in seen and IMAGE_EXT.search(u):
                seen.add(u)
                out.append(u)
    return out


def is_citation(url: str) -> bool:
    """True if this link is a reference to read, not a dependency to monitor."""
    from miw.trust import (CORROBORATING_DOMAINS, EXCLUDED_DOMAINS, LEAD_ONLY_DOMAINS)

    d = domain(url)
    reg = registrable(d)
    if not d:
        return True
    # The trust layer already knows these are sources rather than subjects.
    for group in (EXCLUDED_DOMAINS, CORROBORATING_DOMAINS, LEAD_ONLY_DOMAINS, NEWS_PRESS):
        if reg in group or d in group:
            return True
    if d.endswith(".edu") or d.endswith(".gov") or d.endswith(".gov.in") or d.endswith(".ac.in"):
        return True
    if _PLACEHOLDER.search(d):
        return True
    if any(d.endswith(h) or reg == h for h in _DOCS_HOSTS):
        return True
    return False


def links_in(text: str) -> list[tuple[str, str]]:
    """All external, non-media links in a body of text as (url, syntax).

    Ordered by syntax reliability: an `<a href>` was deliberately authored, whereas a
    bare URL swept out of prose is more likely to be truncated or illustrative.
    """
    if not text or "http" not in text:
        return []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(raw: str, syntax: str) -> None:
        u = normalise_url(raw)
        if not u or u in seen:
            return
        if is_internal(u) or MEDIA_EXT.search(urlparse(u).path):
            return
        seen.add(u)
        found.append((u, syntax))

    for m in A_HREF.finditer(text):
        add(m.group(1) or m.group(2) or m.group(3) or "", "a_href")
    for m in IFRAME.finditer(text):
        add(m.group(1) or m.group(2) or "", "iframe")
    for m in MD_LINK.finditer(text):
        add(m.group(1), "markdown")
    for m in IMG_SRC.finditer(text):
        add(m.group(1) or m.group(2) or "", "img")
    for m in BARE.finditer(text):
        add(m.group(0), "bare")
    return found


# Service naming: turn a registrable domain into a display name a human recognises.
_NAME_OVERRIDES = {
    "n8n.io": "n8n", "openai.com": "OpenAI", "groq.com": "Groq",
    "huggingface.co": "Hugging Face", "google.dev": "Google AI for Developers",
    "langchain.com": "LangChain", "twelvedata.com": "Twelve Data",
    "finnhub.io": "Finnhub", "alpaca.markets": "Alpaca", "serpapi.com": "SerpAPI",
    "murf.ai": "Murf.AI", "openrouter.ai": "OpenRouter", "gradio.app": "Gradio",
    "trychroma.com": "Chroma", "streamlit.io": "Streamlit", "kaggle.com": "Kaggle",
    "anthropic.com": "Anthropic", "deepseek.com": "DeepSeek", "mistral.ai": "Mistral AI",
    "elevenlabs.io": "ElevenLabs", "assemblyai.com": "AssemblyAI", "ollama.com": "Ollama",
    "stability.ai": "Stability AI", "gamma.app": "Gamma", "napkin.ai": "Napkin AI",
    "perplexity.ai": "Perplexity", "deepwiki.com": "DeepWiki",
    "codetotutorial.com": "CodeToTutorial", "gold-api.com": "Gold-API",
    "pypi.org": "PyPI", "github.com": "GitHub", "cloud.google.com": "Google Cloud",
}


def service_name(reg_domain: str) -> str:
    if reg_domain in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[reg_domain]
    stem = reg_domain.rsplit(".", 1)[0].replace("-", " ")
    return " ".join(w.capitalize() if w.islower() else w for w in stem.split())
