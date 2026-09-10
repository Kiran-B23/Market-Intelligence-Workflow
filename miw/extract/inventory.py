"""Build the dependency inventory: the spine of the whole system.

Seven extractors run over the record stream, ordered by how much each can be trusted.
Recall and precision come from different places here, so they are kept apart:

* **Structured signals** (imports, n8n node types, install commands, model ids, links)
  are high precision — a name in an import statement is not a guess.
* **Question tags** are high recall but need the registry to disambiguate a tool from
  a topic: "n8n Platform" is a dependency, "Node Identification" is a lesson. Rather
  than infer toolness from the shape of a tag, unmatched tags go to a review queue.
  Guessing here is how a monitor starts reporting on things that do not exist.
* **Prose names** are matched only against aliases the registry already knows, which
  is what replaces the hand-curated `CATALOG` allowlist that returned empty tool lists
  for PSE.

Deduping is the point of the stage: one dependency, many locations. codetotutorial in
six sessions must be one row with six locations, or the weekly digest reports the same
breakage six times and nobody reads the second one.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from miw.extract import code as C
from miw.extract import n8n as N8N
from miw.extract.links import (images_in, is_citation, is_infra, links_in,
                               registrable, service_name)
from miw.net import domain
from miw.registry import Entry, Registry, norm
from miw.schema import ContentRecord, Dependency, Location

# Model family -> the vendor whose pages are authoritative about it.
MODEL_VENDORS: dict[str, tuple[str, str, str]] = {
    # prefix: (vendor, homepage, official domains csv)
    "gemini": ("Google", "https://ai.google.dev/gemini-api/docs/models", "ai.google.dev,google.dev,deepmind.google,cloud.google.com"),
    "gpt": ("OpenAI", "https://platform.openai.com/docs/models", "openai.com,platform.openai.com"),
    "text-embedding": ("OpenAI", "https://platform.openai.com/docs/models", "openai.com,platform.openai.com"),
    "whisper": ("OpenAI", "https://platform.openai.com/docs/models", "openai.com,platform.openai.com"),
    "claude": ("Anthropic", "https://docs.anthropic.com/en/docs/about-claude/models", "anthropic.com,docs.anthropic.com,claude.ai"),
    "llama": ("Meta", "https://www.llama.com/", "llama.com,ai.meta.com,huggingface.co"),
    "qwen": ("Alibaba", "https://huggingface.co/Qwen", "huggingface.co,qwen.ai"),
    "mistral": ("Mistral AI", "https://docs.mistral.ai/getting-started/models/", "mistral.ai,docs.mistral.ai"),
    "deepseek": ("DeepSeek", "https://api-docs.deepseek.com/quick_start/pricing", "deepseek.com,api-docs.deepseek.com"),
    "all-mpnet": ("Hugging Face", "https://huggingface.co/sentence-transformers", "huggingface.co"),
    "all-minilm": ("Hugging Face", "https://huggingface.co/sentence-transformers", "huggingface.co"),
    "deberta": ("Hugging Face", "https://huggingface.co/protectai", "huggingface.co"),
    "nomic-embed": ("Nomic", "https://docs.nomic.ai/", "nomic.ai,docs.nomic.ai"),
    "embed-english": ("Cohere", "https://docs.cohere.com/docs/models", "cohere.com,docs.cohere.com"),
    "voyage": ("Voyage AI", "https://docs.voyageai.com/docs/embeddings", "voyageai.com,docs.voyageai.com"),
    "gemma": ("Google", "https://ai.google.dev/gemma/docs", "ai.google.dev,google.dev,huggingface.co"),
}

N8N_DOMAINS = ["n8n.io", "docs.n8n.io"]

# Prose matching runs only for these kinds. A package is established by an import or
# an install command, never by the English word that happens to be its name: matching
# the noun "requests" or "datasets" in prose produced hundreds of phantom locations and
# swamped the real evidence.
PROSE_ELIGIBLE_KINDS = {"service", "tool"}

# Aliases that are ordinary words, generic tech nouns, or too ambiguous to match as a
# bare word. Without this, "Python" alone contributed 1206 locations and the word
# "application" arrived as a package.
PROSE_STOP = {
    # ambiguous brand fragments
    "google", "amazon", "claude", "gemini", "meta", "apple", "microsoft", "x",
    "ibm", "oracle", "adobe", "canva", "notion", "slack", "zoom", "gpt", "llama",
    # generic platforms - true dependencies, but not signal when named in prose
    "python", "python.org", "javascript", "typescript", "java", "nodejs", "node.js",
    "html", "css", "json", "yaml", "sql", "linux", "ubuntu", "windows", "macos",
    "docker", "git", "bash", "shell", "jupyter", "vscode", "chrome", "firefox",
    # ordinary English nouns that are also package or product names
    "application", "applications", "requests", "request", "datasets", "dataset",
    "evaluate", "evaluation", "enable", "enabled", "installed", "install", "scoped",
    "express", "flask", "template", "templates", "prompt", "prompts", "agent",
    "agents", "chat", "cloud", "docs", "api", "apis", "model", "models", "tool",
    "tools", "node", "nodes", "memory", "table", "trigger", "audio", "video",
    "image", "images", "search", "browser", "server", "client", "database", "index",
    "vector", "embedding", "embeddings", "token", "tokens", "stream", "streaming",
    "pipeline", "workflow", "workflows", "session", "sessions", "content", "context",
    "output", "outputs", "input", "inputs", "response", "answer", "question",
    "system", "systems", "service", "services", "platform", "project", "projects",
    "score", "scores", "accuracy", "latency", "hosting", "storage", "runtime",
    "bertscore", "rouge", "bleu", "cloudfront", "localhost", "example", "today",
    "time", "reddit", "wikipedia", "youtube", "discord", "render", "vercel", "vite",
    "redis", "axios", "mozilla", "nvidia", "stanford", "reuters", "fortune",
    # Ordinary words the curriculum uses constantly, each of which also happens to be
    # a sheet-declared "tool". Measured prose contributions before this: email 358,
    # http-request 336, fetch 237, webhook 234. They are the same class of false
    # positive as "Python" contributing 1206, just with lower-profile names. The sheet
    # declaration survives; only the prose matching stops.
    "email", "emails", "fetch", "webhook", "webhooks", "excel", "sheet", "sheets",
    "docs", "drive", "calendar", "forms", "slides", "mail", "gmail",
    "http request", "http-request", "if node", "if-node", "mcp client", "mcp-client",
    "switch", "merge", "filter", "code", "wait", "schedule", "form", "chat trigger",
    # Library class and helper names. These are real curriculum content, but they are
    # classes INSIDE langgraph / langchain / trl - there is no vendor page for
    # `InMemorySaver`, so prose matching only inflates their blast radius.
    "inmemorysaver", "humanintheloopmiddleware", "piimiddleware", "sfttrainer",
    "huggingfaceembeddings", "huggingface hub", "chatprompttemplate",
    "recursivecharactertextsplitter", "conversationbuffermemory",
}
PROSE_MIN_LEN = 5

# Hosts that are not curriculum dependencies even though they are external.
SKIP_DOMAINS = {"localhost", "example.com", "example.org", "127.0.0.1", "0.0.0.0", "",
                "t.co", "bit.ly", "tinyurl.com", "cloudfront.net", "akamaihd.net",
                "jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "gstatic.com",
                "googleusercontent.com", "w3.org", "schema.org", "gravatar.com"}


@dataclass
class ExtractStats:
    records: int = 0
    dependencies: int = 0
    locations: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    by_evidence: dict[str, int] = field(default_factory=dict)
    tag_candidates: int = 0
    tags_matched: int = 0
    registry_derived: int = 0
    citations_skipped: int = 0


def _bump(d: dict[str, int], k: str, n: int = 1) -> None:
    d[k] = d.get(k, 0) + n


class InventoryBuilder:
    def __init__(self, registry: Registry):
        self.reg = registry
        # (course, unit_id) -> set of image URLs. Used to tell a reviewer how much
        # step-by-step imagery a docs change puts at risk.
        self.unit_images: dict[str, set[str]] = collections.defaultdict(set)
        self.deps: dict[str, Dependency] = {}
        self.stats = ExtractStats()
        self.tag_candidates: collections.Counter = collections.Counter()
        self.tag_candidate_courses: dict[str, set[str]] = collections.defaultdict(set)
        self._prose_re: Optional[re.Pattern] = None

    # --- dependency plumbing ------------------------------------------------

    def _dep(self, kind: str, name: str, **kw) -> Dependency:
        entry = self.reg.resolve(name, kind) or self.reg.resolve(name)
        if entry:
            name = entry.canonical_name
            kw.setdefault("homepage", entry.homepage)
            kw.setdefault("docs_url", entry.docs_url)
            kw.setdefault("changelog_url", entry.changelog_url)
            kw.setdefault("pricing_url", entry.pricing_url)
            kw.setdefault("status_url", entry.status_url)
            kw.setdefault("official_domains", list(entry.official_domains))
            kw.setdefault("registry", entry.registry)
            kw.setdefault("registry_id", entry.registry_id)
            kw.setdefault("vendor", entry.vendor)
            kw.setdefault("aliases", list(entry.aliases))
            if entry.watch_tier:
                kw.setdefault("watch_tier", entry.watch_tier)
        key = f"{kind}:{norm(name)}"
        dep = self.deps.get(key)
        if dep is None:
            dep = Dependency(kind=kind, canonical_name=name, **kw)
            self.deps[key] = dep
            _bump(self.stats.by_kind, kind)
        else:
            for fld in ("homepage", "docs_url", "changelog_url", "pricing_url", "status_url"):
                if not getattr(dep, fld) and kw.get(fld):
                    setattr(dep, fld, kw[fld])
            if kw.get("official_domains"):
                dep.official_domains = sorted(set(dep.official_domains) | set(kw["official_domains"]))
        return dep

    def _locate(self, dep: Dependency, r: ContentRecord, evidence: str,
                field_path: Optional[str] = None) -> None:
        loc = Location(
            course=r.course, topic_name=r.topic_name, unit_id=r.unit_id,
            unit_name=r.unit_name, content_id=r.content_id,
            field_path=field_path or r.field_path, evidence_source=evidence,
            object_type=r.object_type, session_no=r.session_no,
        )
        before = len(dep.locations)
        dep.merge_location(loc)
        if len(dep.locations) != before:
            self.stats.locations += 1
            _bump(self.stats.by_evidence, evidence)

    # --- extractors ---------------------------------------------------------

    def _links(self, r: ContentRecord) -> None:
        for url, syntax in links_in(r.body_text):
            d = domain(url)
            reg_dom = registrable(d)
            if reg_dom in SKIP_DOMAINS or not reg_dom or is_infra(url):
                continue
            if is_citation(url):
                self.stats.citations_skipped += 1
                continue
            entry = self.reg.by_domain(d) or self.reg.by_domain(reg_dom)
            if entry:
                dep = self._dep(entry.kind, entry.canonical_name)
            else:
                nm = service_name(reg_dom)
                if len(nm) < 3:
                    continue
                # The author's own link is the statement of official authority.
                dep = self._dep(
                    "service", nm,
                    homepage=f"https://{reg_dom}", official_domains=[reg_dom],
                    aliases=[reg_dom, nm],
                )
                self.stats.registry_derived += 1
            if d not in dep.official_domains and registrable(d) in dep.official_domains:
                dep.official_domains = sorted(set(dep.official_domains) | {d})
            if url not in dep.referenced_urls:
                dep.referenced_urls.append(url)
            self._locate(dep, r, f"link:{syntax}")

    def _packages(self, r: ContentRecord) -> None:
        pairs: list[tuple[str, str, str]] = []
        if r.evidence_source == "solution_code":
            pairs += [(p, reg, "solution_import") for p, reg in C.imports(r.body_text)]
        if r.evidence_source in ("markdown", "solution_prose"):
            pairs += [(p, reg, "install_command") for p, reg in C.installs(r.body_text)]
        pins = C.pinned_versions(r.body_text) if pairs else {}
        for name, reg_name, evidence in pairs:
            dep = self._dep("package", name, registry=reg_name, registry_id=name)
            if pins.get(name) and not dep.taught_version:
                dep.taught_version = pins[name]
            self._locate(dep, r, evidence)

    def _models(self, r: ContentRecord) -> None:
        for m in C.models(r.body_text):
            vendor = homepage = domains = ""
            for prefix, (v, hp, doms) in MODEL_VENDORS.items():
                if m.startswith(prefix) or m.split("/")[0].startswith(prefix):
                    vendor, homepage, domains = v, hp, doms
                    break
            dep = self._dep("model", m, vendor=vendor, docs_url=homepage,
                            homepage=homepage,
                            official_domains=domains.split(",") if domains else [])
            self._locate(dep, r, "model_id")

    def _n8n(self, r: ContentRecord) -> None:
        if "n8n-nodes" not in r.body_text:
            return
        for node_type, tv in N8N.nodes(r.body_text):
            dep = self._dep("n8n_node", node_type, registry="n8n",
                            registry_id=N8N.package_of(node_type),
                            official_domains=list(N8N_DOMAINS),
                            homepage="https://n8n.io",
                            docs_url="https://docs.n8n.io/integrations/",
                            changelog_url="https://docs.n8n.io/release-notes/",
                            vendor="n8n")
            if tv and not dep.taught_version:
                dep.taught_version = tv
            self._locate(dep, r, "n8n_workflow")

    def _tag(self, r: ContentRecord) -> None:
        name = r.body_text.strip()
        # Machine-generated tag ids carry no tool information.
        if re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)+", name) or len(name) < 3:
            return
        entry = self.reg.resolve(name)
        if entry:
            self.stats.tags_matched += 1
            self._locate(self._dep(entry.kind, entry.canonical_name), r, "question_tag")
            return
        self.tag_candidates[name] += 1
        self.tag_candidate_courses[name].add(r.course)

    def _prose(self, r: ContentRecord) -> None:
        """Registry-alias matching in prose. Replaces the CATALOG allowlist."""
        if self._prose_re is None:
            self._build_prose_re()
        if not self._prose_re:
            return
        low = r.body_text.lower()
        for m in self._prose_re.finditer(low):
            entry = self._prose_map.get(m.group(0))
            if entry:
                self._locate(self._dep(entry.kind, entry.canonical_name), r, "prose_name")

    def _build_prose_re(self) -> None:
        self._prose_map: dict[str, Entry] = {}
        for key, entry in self.reg.alias_index().items():
            if entry.kind not in PROSE_ELIGIBLE_KINDS:
                continue
            if len(key) < PROSE_MIN_LEN or key in PROSE_STOP or key.isdigit():
                continue
            if norm(entry.canonical_name) in PROSE_STOP:
                continue
            self._prose_map[key] = entry
        if not self._prose_map:
            self._prose_re = re.compile(r"(?!x)x")
            return
        alts = sorted(self._prose_map, key=len, reverse=True)
        self._prose_re = re.compile(
            r"(?<![\w.\-])(" + "|".join(re.escape(a) for a in alts) + r")(?![\w\-])")

    # --- orchestration ------------------------------------------------------

    def feed_structured(self, records: Iterable[ContentRecord]) -> None:
        """Pass 1: signals that stand on their own, needing no registry."""
        for r in records:
            self.stats.records += 1
            if r.evidence_source == "question_tag":
                self._tag(r)
                continue
            if r.evidence_source == "test_case_enum":
                entry = self.reg.resolve(r.body_text.strip().replace("_", " "))
                if entry:
                    self._locate(self._dep(entry.kind, entry.canonical_name), r, "test_case_enum")
                continue
            for img in images_in(r.body_text):
                self.unit_images[f"{r.course}|{r.unit_id}"].add(img)
            self._links(r)
            self._packages(r)
            self._models(r)
            self._n8n(r)

    def feed_sheets(self, sheet_tools, workbook_courses: dict[str, str],
                    session_of_name: Optional[dict] = None) -> None:
        """Fold in the workbooks' hand-maintained tool columns.

        Sheets contribute two things the JSON export cannot: names of no-code and SaaS
        tools that appear only on slides, and **version pins recorded by hand at
        authoring time** (`n8n@2.17.8`, `gradio@6.6.0`). A sheet asserts that a session
        uses a tool; it says nothing about where that tool's official pages live, so a
        sheet-only entry gets no authority set and therefore cannot substantiate a
        strict claim until a human adds one.

        `session_of_name` maps `(course, lowercased session or unit name)` -> session
        number, built by the caller from the already-ingested records. Without it every
        sheet-derived Location had `session_no = None`, so 43 dependencies in Intro to
        Gen AI - `Cerebras`, `Suno`, `gpt-4o` among them - were reported as being in the
        course with no way to say *where*, and the UI showed "workbook" where a session
        belonged. The tool sheets name the session in their own words ("Mastering Image
        Generation", "AI News Summarizer"), which the records can resolve: measured on
        Intro to Gen AI, all 361 declarations place.
        """
        names = session_of_name or {}
        for t in sheet_tools:
            # A hand-recorded pin is a package version, so when a name resolves to
            # both a hosted service and a distribution, the pin belongs to the
            # distribution: `Groq@0.37.1` is the SDK, not the API service.
            entry = (self.reg.resolve(t.name, "package") if t.taught_version else None) \
                or self.reg.resolve(t.name)
            kind = entry.kind if entry else "tool"
            name = entry.canonical_name if entry else t.name
            dep = self._dep(kind, name)
            if t.taught_version and not dep.taught_version:
                dep.taught_version = t.taught_version
            if not entry:
                self.reg.add(Entry(canonical_name=name, kind=kind,
                                   aliases=[t.name], review_status="from_sheet",
                                   notes=f"declared in {t.workbook} / {t.sheet}"))
                self._prose_re = None
            # No fallback to the workbook name. That fallback is what turned a stale
            # filename map into three phantom courses; skipping is recoverable, a
            # phantom course silently corrupts every per-course number.
            course = workbook_courses.get(t.workbook, "")
            if not course:
                continue
            loc = Location(
                course=course, topic_name="(from workbook)", unit_id="",
                unit_name=t.session or t.sheet, content_id="",
                field_path=f"{t.workbook}::{t.sheet}",
                evidence_source="sheet_pin" if t.taught_version else "sheet_declared",
                object_type="SHEET",
                session_no=names.get((course, (t.session or "").strip().lower())))
            before = len(dep.locations)
            dep.merge_location(loc)
            if len(dep.locations) != before:
                self.stats.locations += 1
                _bump(self.stats.by_evidence, loc.evidence_source)

    def sync_registry(self) -> None:
        """Fold everything pass 1 discovered back into the registry.

        This is what makes prose matching work on a cold start. Pass 1 learns that
        `groq.com` is Groq from a link; pass 2 can then recognise the bare word "Groq"
        in a session that never linked it. Without the round trip, a fresh checkout
        with an empty registry would find nothing in prose - which is precisely how the
        old CATALOG allowlist came to return empty tool lists for PSE.
        """
        for dep in self.deps.values():
            self.reg.add(Entry(
                canonical_name=dep.canonical_name, kind=dep.kind,
                aliases=sorted(set(dep.aliases)), homepage=dep.homepage,
                docs_url=dep.docs_url, changelog_url=dep.changelog_url,
                pricing_url=dep.pricing_url, status_url=dep.status_url,
                official_domains=sorted(set(dep.official_domains)),
                registry=dep.registry, registry_id=dep.registry_id,
                vendor=dep.vendor, review_status="derived",
            ))
        self._prose_re = None          # force a rebuild against the enriched aliases

    def feed_prose(self, records: Iterable[ContentRecord]) -> None:
        """Pass 2: registry-alias matching in prose, and tag re-resolution."""
        for r in records:
            if r.evidence_source == "question_tag":
                name = r.body_text.strip()
                if name in self.tag_candidates:
                    entry = self.reg.resolve(name)
                    if entry:
                        del self.tag_candidates[name]
                        self.tag_candidate_courses.pop(name, None)
                        self.stats.tags_matched += 1
                        self._locate(self._dep(entry.kind, entry.canonical_name),
                                     r, "question_tag")
                continue
            if r.evidence_source in ("markdown", "solution_prose", "title"):
                self._prose(r)

    def image_census(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.unit_images.items() if v}

    def finish(self) -> list[Dependency]:
        for dep in self.deps.values():
            dep.watch_tier = watch_tier_for(dep)
            dep.official_domains = sorted({d for d in dep.official_domains if d})
            if not dep.official_domains and dep.kind not in ("package",):
                dep.notes = ("no official domain known - cannot substantiate "
                             "deprecation/pricing/version claims until one is set")
        self.stats.dependencies = len(self.deps)
        self.stats.tag_candidates = len(self.tag_candidates)
        return sorted(self.deps.values(),
                      key=lambda d: (-len(d.locations), d.kind, d.canonical_name.lower()))


# A student actually executes the taught step against these.
# A version pin recorded by hand in a workbook means someone actually ran this at
# authoring time - stronger than a passing mention, and the only source for pins at
# all: they cannot be recovered from the JSON export.
RUNTIME_EVIDENCE = {"solution_import", "n8n_workflow", "test_case_enum",
                    "install_command", "sheet_pin"}
# A student opens these to follow the material.
VISITED_EVIDENCE = {"link:a_href", "link:iframe", "model_id"}
# Weak signals: the name appears, but nothing is shown to depend on it.
WEAK_EVIDENCE = {"prose_name", "question_tag", "link:bare", "link:markdown", "title",
                 "sheet_declared"}


def watch_tier_for(dep: Dependency) -> str:
    """How much research budget this dependency earns.

    `critical` means a student executes the taught step against it, so a break becomes
    a support ticket rather than a stale sentence.

    Being *named* inside a graded question does not qualify. An MCQ stem that mentions
    a tool is prose that happens to be graded; treating it as runtime evidence put 221
    of 281 dependencies into `critical` and made the tier meaningless.
    """
    evidence = {l.evidence_source for l in dep.locations}
    if evidence & RUNTIME_EVIDENCE:
        return "critical"
    # Student must open the tool to answer a graded item.
    if any(l.is_graded and l.evidence_source in VISITED_EVIDENCE for l in dep.locations):
        return "critical"
    if evidence & VISITED_EVIDENCE:
        return "standard"
    return "mention-only"
