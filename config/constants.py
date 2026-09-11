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
# PSE is deliberately NOT declared here. Its export still sits in `data/courses/`, inert
# until someone registers it — `cmd_ingest` iterates this roster, so an unregistered
# export is never opened. It was removed to exercise the Add Course flow end to end
# against a real course rather than a copy of one.
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

# Our own asset and delivery hosts. Never treated as curriculum dependencies.
INTERNAL_HOSTS = (
    "ccbp.in", "nxtwave.co.in", "niat.tech", "earlywave.in",
    "nkb-backend-ccbp-media-static.s3.amazonaws.com",
    "media-content.ccbp.in", "learning.ccbp.in", "nkb-backend-ccbp-beta.earlywave.in",
)

# Hosts that are real dependencies but whose per-URL health is not a curriculum
# signal on its own (a single dead Google Slides deck is a content bug, not tool drift).
INFRA_HOSTS = ("docs.google.com", "drive.google.com", "amazonaws.com")
