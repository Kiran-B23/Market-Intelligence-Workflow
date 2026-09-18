"""Course roster and shared constants."""

# `expect_sessions` is the CURRICULUM's own session count - the number of rows in the
# workbook's `Course Outline` sheet - not the number of units that look like a session
# in the JSON export. The two differ, and the earlier values here were calibrated to the
# export's positional count, so the integrity check passed while every session number
# after the divergence was reported one too high:
#
#   Intro to Gen AI   export 26 video sessions, workbook numbers 25
#                     (`Common Mistakes` is a LEARNING_SET with a video, not a session)
#   AI for Finance    export 18, workbook 17 (`AI Finance Add-On Session`)
#   LLM Applications  export 29, workbook 29 - agrees
#
# Only registered courses are read. `cmd_ingest` iterates this roster, so an export
# sitting in `data/courses/` without an entry here is never opened — which is what lets
# a course be added through the UI's Add Course flow instead, landing in the writable
# overlay rather than in this tracked literal.
#
# This literal stays HAND-OWNED. Courses added through the UI land in the writable
# overlay `data/course_registry.json` instead, and `miw.courses.Roster` merges the two
# with these entries winning on conflict — an untracked file must not be able to
# override a tracked number whose justification is written directly above it.
_DECLARED = {
    "intro_to_gen_ai":  {"title": "Intro to Gen AI",            "expect_sessions": 25},
    "llm_applications": {"title": "Building LLM Applications",  "expect_sessions": 29},
    "ai_for_finance":   {"title": "AI for Finance",             "expect_sessions": 17},
}

# `COURSES` is still the only name anything imports, so no caller changed. It is a dict
# subclass that re-reads the overlay when its mtime moves, which is what lets a course
# registered through the API be visible to a long-running uvicorn without a restart.
from miw.courses import Roster  # noqa: E402  (after _DECLARED, by necessity)

COURSES = Roster(_DECLARED)

# What a dependency is FOR, as a closed vocabulary.
#
# PRD §29 records a nominator that was built, measured and abandoned for want of exactly
# this field: "Murf.AI and ElevenLabs are alternatives, Murf.AI and Lovable are co-taught
# in one project. Nothing in the inventory distinguishes them and the registry has no
# capability field to lean on." Co-occurrence conflates substitutes with complements, and
# those are opposites. `kind` separates tool/service/package/model/n8n_node — what a
# thing IS. This separates what it is FOR, which is the axis a replacement question turns
# on.
#
# CLOSED on purpose. An open text field becomes 474 spellings of the same dozen ideas,
# and the entire value here is grouping. Adding a term is a deliberate edit to this list,
# not something a typo can do.
CAPABILITIES = (
    "llm-api",              # a hosted model endpoint: OpenAI, Groq, Gemini API
    "agent-framework",      # LangChain, CrewAI, LangGraph
    "no-code-automation",   # n8n, Make, Zapier
    "vector-db",            # Chroma, Pinecone, Qdrant
    "voice-synthesis",      # Murf.AI, ElevenLabs, F5-TTS
    "speech-recognition",   # Whisper, AssemblyAI
    "image-generation",     # Stable Diffusion, Midjourney, FLUX
    "search-api",           # SerpAPI, Tavily, ScraperAPI
    "market-data",          # Twelve Data, Alpaca
    "observability",        # LangSmith, Langfuse
    "doc-processing",       # unstructured, PyPDF, text splitters
    "deployment",           # Render, Vercel, Streamlit Cloud
    "tunnelling",           # ngrok, localtunnel
    "ide",                  # Cloud IDE, Cursor, Claude Code
    "productivity",         # Google Docs/Sheets/Calendar, Notion
    "messaging",            # Telegram, Slack, Gmail
    "model-hub",            # Hugging Face, Kaggle
    "source-control",       # GitHub
)

# Our own asset and delivery hosts. Never treated as curriculum dependencies.
INTERNAL_HOSTS = (
    "ccbp.in", "nxtwave.co.in", "niat.tech", "earlywave.in",
    "nkb-backend-ccbp-media-static.s3.amazonaws.com",
    "media-content.ccbp.in", "learning.ccbp.in", "nkb-backend-ccbp-beta.earlywave.in",
)

# Hosts that are real dependencies but whose per-URL health is not a curriculum
# signal on its own (a single dead Google Slides deck is a content bug, not tool drift).
INFRA_HOSTS = ("docs.google.com", "drive.google.com", "amazonaws.com")


# What the reviewer actually has to open. `object_type` is the CMS's own word for the
# kind of content object a location sits in; these are the words a curriculum reviewer
# uses for the same thing.
#
# This exists because `recommend()` used to print the location's `unit_name`, and in
# this curriculum the unit holding the MCQ bank is called "Coding Practice". The result
# was 25 of 43 findings announcing "Starts at ... / Coding Practice" while every one of
# them pointed at a quiz question and not one pointed at a coding question. The unit's
# name is a label someone chose; the artifact type is a fact we recorded.
ARTIFACT_WORDS = {
    "OBJECTIVE_QUESTIONS": "quiz question",
    "CODING_QUESTIONS": "coding practice",
    "LEARNING_RESOURCE": "reading material",
    "SESSION_PPT": "slide deck",
    "SHEET": "tracking sheet row",
    "UNIT_TAG": "unit tag",
}
ARTIFACT_WORDS_PLURAL = {
    "OBJECTIVE_QUESTIONS": "quiz questions",
    "CODING_QUESTIONS": "coding practices",
    "LEARNING_RESOURCE": "reading materials",
    "SESSION_PPT": "slide decks",
    "SHEET": "tracking sheet rows",
    "UNIT_TAG": "unit tags",
}
# The order a reviewer works in: the things that break a student first.
ARTIFACT_ORDER = ("CODING_QUESTIONS", "OBJECTIVE_QUESTIONS", "LEARNING_RESOURCE",
                  "SESSION_PPT", "SHEET", "UNIT_TAG")


def artifact_word(object_type: str, plural: bool = False) -> str:
    table = ARTIFACT_WORDS_PLURAL if plural else ARTIFACT_WORDS
    return table.get(object_type, "place" + ("s" if plural else ""))
