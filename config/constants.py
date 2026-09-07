"""Course roster and shared constants."""

COURSES = {
    "intro_to_gen_ai":  {"title": "Intro to Gen AI",            "expect_sessions": 26},
    "llm_applications": {"title": "Building LLM Applications",  "expect_sessions": 29},
    "ai_for_finance":   {"title": "AI for Finance",             "expect_sessions": 18},
    "pse":              {"title": "PSE",                        "expect_sessions": 13},
}

# Our own asset and delivery hosts. Never treated as curriculum dependencies.
INTERNAL_HOSTS = (
    "ccbp.in", "nxtwave.co.in", "niat.tech", "earlywave.in",
    "nkb-backend-ccbp-media-static.s3.amazonaws.com",
    "media-content.ccbp.in", "learning.ccbp.in", "nkb-backend-ccbp-beta.earlywave.in",
)

# Hosts that are real dependencies but whose per-URL health is not a curriculum
# signal on its own (a single dead Google Slides deck is a content bug, not tool drift).
INFRA_HOSTS = ("docs.google.com", "drive.google.com", "amazonaws.com")
