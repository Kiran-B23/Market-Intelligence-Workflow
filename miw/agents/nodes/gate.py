"""The routing decisions. Plain functions returning a node name — never a model call.

This is the load-bearing difference from the prior Curriculum Gap Analyzer, whose critic
node asked a model "is this evidence hallucinated?" while being passed only the source
URL, and then kept the finding when the answer was missing or the call crashed. Here the
question "did we substantiate anything" is answered by counting substantiated claims.
"""
from __future__ import annotations


def route_after_plan(state: dict) -> str:
    """Where to go after planning.

    `no_source` and `blocked` route STRAIGHT OUT rather than through `give_up`, because
    they are already-final statuses and `give_up` would relabel them `exhausted` —
    losing the distinction between "we cannot speak for this subject at all" (203 of
    460 dependencies), "the model was unavailable", and "we looked and found nothing".
    Those three need different follow-up, so they must not collapse into one word.
    """
    if state.get("status") in ("no_source", "blocked"):
        return "stop"
    if state.get("plan_says_nothing") and not state.get("planned_urls"):
        return "record_silence"
    return "read" if state.get("planned_urls") else "give_up"


def route_after_read(state: dict) -> str:
    """Verified, out of attempts, or worth one more plan."""
    if state.get("claims"):
        return "done"
    if int(state.get("attempt", 0)) >= int(state.get("max_attempts", 2)):
        return "give_up"
    return "replan"


def record_silence_node(state: dict) -> dict:
    """"This vendor publishes nothing" is a result, cached so we stop guessing.

    Recorded as a refutation rather than a claim: it is a fact about our own search, not
    about the curriculum, and it must never reach a finding.
    """
    return {"status": "no_source",
            "refuted": [f"{state.get('canonical_name','?')}: the model reports this "
                        f"vendor publishes no change/deprecation page "
                        f"({state.get('plan_reason','no reason given')})"],
            "trajectory": ["recorded: vendor appears to publish nothing"]}


def give_up_node(state: dict) -> dict:
    return {"status": "exhausted",
            "trajectory": [f"gave up after {state.get('attempt', 0)} attempt(s), "
                           f"{len(state.get('pages_read') or [])} page(s) read"]}
