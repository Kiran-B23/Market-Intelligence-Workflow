"""Where did the page go?

A dead link finding used to say "repoint or replace it" and stop. That is the reviewer's
whole job handed back to them: they open the URL, see a 404, and then do by hand what we
are better placed to do - try the obvious candidates on the vendor's own site and see
which one answers.

Two rules make this safe to act on:

* **Only URLs we fetched ourselves, that answered 200, on a domain the dependency
  already owns.** That is a first-hand observation, which this system treats as needing
  no citation - the same standing the probe's own 404 has. A candidate on somebody
  else's domain is a guess about where a vendor moved its content, and a wrong guess
  here sends a student somewhere the course never intended.
* **Never silently.** A suggestion says which rule produced it, so a reviewer can see
  the difference between "this is the redirect target the server itself named" and "the
  deep path is gone and this is the section above it".

The candidates, strongest first, are all deterministic. No model is involved and no
search is spent: this runs inside the probe, which is the stage that costs nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from miw.net import domain

# A URL the course prints as an EXAMPLE rather than links to. `abc123.ngrok.io` and
# `xxxxx.gradio.live` are both in the live inventory, both recorded as dead links, and
# neither has ever been a page: they are the shape of a tunnel URL a student will
# generate themselves. Repointing them is not the fix - the fix is to stop treating them
# as links at all - so they are reported as placeholders and no successor is sought.
PLACEHOLDER_HOSTS = (
    "ngrok.io", "ngrok-free.app", "gradio.live", "trycloudflare.com",
    "loca.lt", "localtunnel.me", "serveo.net", "localhost.run", "ngrok.app",
)
_PLACEHOLDER_LABEL = re.compile(
    r"^(?:x{3,}|y{3,}|z{3,}|abc\d*|123\w*|your[-_]?\w*|my[-_]?\w*|example\w*|"
    r"foo\w*|bar\w*|test\w*|demo\w*|sample\w*|\w*placeholder\w*|[a-z]{1,3}\d{2,6})$")


def is_placeholder(url: str) -> bool:
    """A URL nobody can repoint, because it was never a real address.

    Two independent tests, because either alone is wrong: a real ngrok URL exists for
    somebody, and `abc123.example.com` might be a real host. It takes an ephemeral
    tunnel host AND a label that reads as a stand-in.
    """
    host = (domain(url) or "").lower()
    if not host:
        return False
    if not any(host == h or host.endswith("." + h) for h in PLACEHOLDER_HOSTS):
        return False
    label = host.split(".")[0]
    return bool(_PLACEHOLDER_LABEL.match(label))


@dataclass
class Successor:
    """One candidate, with how it was found and what came back."""
    url: str
    rule: str                       # redirect | same_path_on_home | trimmed | home
    status: Optional[int] = None
    title: str = ""

    @property
    def note(self) -> str:
        return {
            "redirect": "the server redirected here",
            "same_path_on_home": "the same path on the vendor's primary domain",
            "trimmed": "the section above the dead path",
            "home": "the product's home page; the specific page is gone",
        }.get(self.rule, self.rule)


@dataclass
class SuccessorSearch:
    dead_url: str
    placeholder: bool = False
    tried: list = field(default_factory=list)      # every candidate URL, in order
    found: Optional[Successor] = None


def _same_site(url: str, official: set) -> bool:
    d = (domain(url) or "").lower()
    return bool(d) and any(d == o or d.endswith("." + o) for o in official if o)


def _candidates(dead_url: str, final_url: str, homepage: str) -> list:
    """(url, rule) pairs, strongest first, deduplicated, original excluded."""
    out: list = []
    seen = {dead_url.rstrip("/")}

    def add(u: str, rule: str) -> None:
        if not u:
            return
        key = u.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            out.append((u, rule))

    # 1. The server's own answer. It may itself 404 - Composio's dashboard redirects to
    #    `composio.dev/toolkits/dashboard`, which is also gone - so it is a candidate to
    #    be checked, never an answer to be reported.
    if final_url and final_url.rstrip("/") != dead_url.rstrip("/"):
        add(final_url, "redirect")

    parts = urlsplit(dead_url)
    path = parts.path or "/"

    # 2. The same path on the primary domain. A vendor consolidating subdomains -
    #    `mcp.composio.dev/x` -> `composio.dev/x` - is the common shape.
    if homepage:
        hp = urlsplit(homepage)
        if hp.netloc and hp.netloc != parts.netloc:
            add(urlunsplit((hp.scheme or "https", hp.netloc, path, "", "")),
                "same_path_on_home")

    # 3. Walk up the dead path. `/a/b/c` -> `/a/b` -> `/a`.
    segs = [s for s in path.split("/") if s]
    for i in range(len(segs) - 1, 0, -1):
        add(urlunsplit((parts.scheme or "https", parts.netloc,
                        "/" + "/".join(segs[:i]), "", "")), "trimmed")

    # 4. The front doors. Last, and labelled as an admission rather than an answer.
    add(urlunsplit((parts.scheme or "https", parts.netloc, "/", "", "")), "home")
    if homepage:
        add(homepage, "home")
    return out


MAX_TRIES = 6


def find_successor(dead_url: str, *, final_url: str = "", homepage: str = "",
                   official_domains=(), observer: Optional[Callable] = None,
                   ) -> SuccessorSearch:
    """The live page that replaced `dead_url`, or nothing.

    `observer` defaults to the probe's own `observe`, so this is one more HTTP check in
    the stage that already makes them - no model, no search, no new budget.
    """
    from miw.probe.http_probe import observe as _observe
    look = observer or _observe

    out = SuccessorSearch(dead_url=dead_url)
    if is_placeholder(dead_url):
        # Nothing to find. The course is printing an example, and the finding it needs
        # is "this is not a link", not "here is a better link".
        out.placeholder = True
        return out

    official = {(o or "").lower() for o in official_domains}
    official |= {(domain(homepage) or "").lower()} - {""}

    for url, rule in _candidates(dead_url, final_url, homepage)[:MAX_TRIES]:
        # A vendor's content may only be replaced by that vendor's own pages.
        if official and not _same_site(url, official):
            continue
        out.tried.append(url)
        try:
            obs = look(url)
        except Exception:                       # a probe failure is not an answer
            continue
        if obs.status == 200 and not obs.gone and obs.reachable and not obs.parked:
            out.found = Successor(url=obs.final_url or url, rule=rule,
                                  status=obs.status, title=(obs.title or "")[:120])
            break
    return out
