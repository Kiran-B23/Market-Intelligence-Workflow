"""Package extraction from reference solution code and install commands.

Two sources, very different precision:

* `solutions[].solution_answer` holds the actual reference implementation. Its import
  statements are the highest-precision dependency evidence in the export — a session
  cannot work if an imported package is gone. This source was previously unmined.
* `pip install` lines in the markdown. The `PIP` regex and the `NOT_A_PACKAGE`
  stopword set are vendored from `Market Intelligence Workflow/extract_tools.py`,
  where they were tuned against real course prose.
"""
from __future__ import annotations

import re

# Anchored at a line/fence/prompt boundary. Unanchored, `pip install` matches inside a
# sentence ("...you can pip install the application yourself") and the tail of the
# sentence arrives in the inventory as packages named "application" and "installed".
PIP = re.compile(
    r"(?:^|[`$>\n]|\$\s)\s*!?\s*(?:python\s+-m\s+)?pip3?\s+install\s+"
    r"((?:-{1,2}[\w-]+\s+)*)([^\n`|<>;]+)", re.I | re.M)
NPM = re.compile(
    r"(?:^|[`$>\n]|\$\s)\s*!?\s*(?:npm|pnpm|yarn)\s+(?:install|i|add)\s+"
    r"((?:-{1,2}[\w-]+\s+)*)([^\n`|<>;]+)", re.I | re.M)

NOT_A_PACKAGE = set("""and or the for with from into using use to a an of in on as is are be
package packages library libraries install installs terminal bash python colab notebook cell
cells run runs first before after next then also we you it this that all core api key
npm npx node pip dev save global latest starts start run build serve test http https
your our new项 version versions upgrade force quiet user requirements txt file""".split())

# A distribution name. Guards both extractors: without it, a stray regex match such as
# ": " or a prose word arrives in the inventory looking exactly like a real package.
DIST_NAME = re.compile(r"^[a-z][a-z0-9]*([._-][a-z0-9]+)*(\[[\w,]+\])?$")
NPM_SCOPED = re.compile(r"^@[a-z0-9][\w.-]*/[a-z0-9][\w.-]*$")


def _valid_dist(name: str, registry: str) -> bool:
    if not name or len(name) < 2 or name in NOT_A_PACKAGE:
        return False
    if registry == "npm" and name.startswith("@"):
        return bool(NPM_SCOPED.match(name))
    return bool(DIST_NAME.match(name))

PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))",
    re.M)
JS_IMPORT = re.compile(r"""(?:from|require\()\s*['"]([^'"./][^'"]*)['"]""")

# Python's standard library: imported constantly, never a curriculum dependency.
STDLIB = set("""os sys re json time datetime math random typing pathlib collections
itertools functools subprocess shutil glob io base64 hashlib uuid logging warnings
dataclasses enum abc copy csv sqlite3 textwrap string threading asyncio concurrent
urllib http socket ssl argparse getpass tempfile pickle statistics decimal fractions
zoneinfo contextlib inspect traceback operator heapq bisect struct array unicodedata
secrets shlex signal platform importlib types weakref gc ast codecs difflib pprint
zipfile tarfile gzip binascii ipaddress numbers cmath calendar locale gettext""".split())

# Import name -> distribution name. Only where they differ.
IMPORT_TO_DIST = {
    "sklearn": "scikit-learn", "PIL": "pillow", "cv2": "opencv-python",
    "bs4": "beautifulsoup4", "yaml": "pyyaml", "dotenv": "python-dotenv",
    "fitz": "pymupdf", "pypdf2": "pypdf", "google": "google-genai",
    "serpapi": "google-search-results", "faiss": "faiss-cpu",
    "sentence_transformers": "sentence-transformers", "huggingface_hub": "huggingface-hub",
    "langchain_community": "langchain-community", "langchain_core": "langchain-core",
    "langchain_google_genai": "langchain-google-genai", "langchain_groq": "langchain-groq",
    "langchain_openai": "langchain-openai", "langchain_chroma": "langchain-chroma",
    "langchain_huggingface": "langchain-huggingface", "langchain_text_splitters":
    "langchain-text-splitters", "langgraph": "langgraph", "chromadb": "chromadb",
    "psycopg2": "psycopg2-binary", "Crypto": "pycryptodome", "jwt": "pyjwt",
    "attr": "attrs", "pkg_resources": "setuptools", "IPython": "ipython",
}


MAX_INSTALL_ARGS = 12


def _clean_install(rest: str) -> list[str]:
    """Package arguments of one install command.

    **Stops** at the first token that is not a plausible package rather than filtering
    and continuing. A real command is a contiguous run of package names; the moment a
    prose word appears we have left the command and everything after it is English.
    """
    out: list[str] = []
    for tok in rest.split()[:MAX_INSTALL_ARGS]:
        if tok.startswith("-"):
            continue
        low = tok.lower().split("==")[0].split(">=")[0].split("~=")[0].strip("\"'()[],")
        if not low or low in NOT_A_PACKAGE or not re.fullmatch(
                r"[a-z@][\w.\-/]*(\[[\w,]+\])?", low):
            break
        out.append(low)
    return out


def installs(text: str) -> list[tuple[str, str]]:
    """(package, registry) pairs from install commands."""
    out: list[tuple[str, str]] = []
    for _flags, rest in PIP.findall(text or ""):
        out += [(p, "pypi") for p in _clean_install(rest) if _valid_dist(p, "pypi")]
    for _flags, rest in NPM.findall(text or ""):
        out += [(p, "npm") for p in _clean_install(rest) if _valid_dist(p, "npm")]
    return out


_PIN = re.compile(r"(?<![\w.])([A-Za-z][\w.\-]*)\s*==\s*(\d[\w.]*)")


def pinned_versions(text: str) -> dict[str, str]:
    """Versions the content actually pins, e.g. `pip install gradio==6.6.0`.

    Scanned **only inside install commands**. Scanning the whole body instead reads
    Python equality comparisons as pins — `response.status_code == 200` becomes
    `response.status_code@200` — and a fabricated version is the one output this
    system must never produce. The existing course sheets carry hand-recorded pins
    (`n8n@2.17.8`) that cannot be recovered from the export at all; a blank is the
    honest answer there.
    """
    out: dict[str, str] = {}
    for rx in (PIP, NPM):
        for _flags, rest in rx.findall(text or ""):
            for m in _PIN.finditer(rest):
                name, ver = m.group(1).lower(), m.group(2)
                if _valid_dist(name.split("[")[0], "pypi") and ("." in ver or len(ver) > 1):
                    out[name] = ver
    return out


def imports(code: str) -> list[tuple[str, str]]:
    """(distribution, registry) pairs from source code imports."""
    out: list[tuple[str, str]] = []
    if not code:
        return out
    for m in PY_IMPORT.finditer(code):
        mods = m.group(1) or m.group(2) or ""
        for mod in mods.split(","):
            top = mod.strip().split(".")[0]
            if not top or top in STDLIB or top.startswith("_"):
                continue
            dist = IMPORT_TO_DIST.get(top, top.lower())
            if _valid_dist(dist, "pypi"):
                out.append((dist, "pypi"))
    for m in JS_IMPORT.finditer(code):
        spec = m.group(1)
        name = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
        if _valid_dist(name.lower(), "npm"):
            out.append((name.lower(), "npm"))
    seen, uniq = set(), []
    for pair in out:
        if pair not in seen:
            seen.add(pair)
            uniq.append(pair)
    return uniq


MODEL = re.compile(
    r"\b(gemini-[\w.\-]+|gpt-[\w.\-]+|claude-[\w.\-]+|llama-?[\d][\w.\-]*|"
    r"qwen[\w.\-]*(?:/[\w.\-]+)?|mistral-[\w.\-]+|deepseek-[\w.\-]+|"
    r"text-embedding-[\w.\-]+|all-mpnet-base-v2|all-MiniLM-[\w.\-]+|"
    r"deberta-v3-base-prompt-injection-v2|whisper-[\w.\-]+|"
    r"nomic-embed-text|embed-english-v[\d.]+|voyage-[\w.\-]+)", re.I)

_MODEL_TRAIL = re.compile(r"[.,;:)\]}'\"]+$")


_MODEL_NOT = ("api", "key", "docs", "doc", "pricing", "billing", "console",
              "quickstart", "reference", "guide", "cookbook", "tutorial")


def models(text: str) -> list[str]:
    """Model ids named in the text.

    A model id must carry a digit: `gemini-2.5-flash` is a dependency we can check a
    deprecation page for, whereas `gemini-api` (swept out of a docs URL by the same
    family prefix) is neither a model nor checkable.
    """
    out = set()
    for m in MODEL.findall(text or ""):
        m = _MODEL_TRAIL.sub("", m).lower().rstrip("-/")
        if len(m) <= 4 or not any(ch.isdigit() for ch in m):
            continue
        if any(w in m.split("-") or m.endswith(w) for w in _MODEL_NOT):
            continue
        out.add(m)
    return sorted(out)
