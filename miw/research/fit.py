"""Does the candidate do the taught job? The model estimates; Python does the sums.

The split is borrowed from the prior Curriculum Gap Analyzer, which is the one genuinely
good idea in it: the model emits only bounded per-factor estimates and Python owns the
arithmetic and the thresholds. Two of its mistakes are deliberately not copied.

  1. It substitutes 0.5 for a factor it could not parse, which makes a real 0.5
     indistinguishable from a crash - and `when_to_act` then stamps a 90-day deadline
     on a score that never existed. Here an unparseable factor is None, None is
     excluded from the mean with the remaining weights renormalised, and too many
     missing factors means the whole score is None rather than a plausible number.
  2. It never clamps, so a model returning 1.5 inflates the total unchallenged.

The weights are set for THIS decision - substituting one tool for another in a taught
session - not carried over from that project's curriculum-gap weighting.

Nothing here can produce a `Claim`: the output is an `AlternativeOpinion`, which has no
source_url and no quote and therefore cannot be built into one.
"""
from __future__ import annotations

from typing import Iterable, Optional

from miw.schema import Alternative, AlternativeOpinion, Dependency, utcnow

WEIGHTS = {
    "does_taught_job": 0.40,
    "free_student_path": 0.25,
    "signup_friction": 0.15,
    "maturity": 0.10,
    "coverage_of_steps": 0.10,
}

# Below this many known factors the score is not reported at all. Two of five, with
# `does_taught_job` missing, is not an assessment - it is a shrug with a number on it.
MIN_KNOWN_FACTORS = 3

# The model says "yes" only when the weighted score clears this AND it judged the
# central factor. Deliberately high: recommending a worse tool confidently is the
# failure mode with the longest half-life.
FIT_THRESHOLD = 0.60


def _clamp(v: object) -> Optional[float]:
    """A factor in [0,1], or None. Anything unparseable is None, never a default."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f:                       # NaN
        return None
    return min(1.0, max(0.0, f))


def score(factors: dict) -> tuple[Optional[float], dict, list[str]]:
    """(fit_score, cleaned factors, unknown names). Weights renormalise over knowns."""
    cleaned: dict[str, Optional[float]] = {}
    unknown: list[str] = []
    for name in WEIGHTS:
        v = _clamp((factors or {}).get(name))
        cleaned[name] = v
        if v is None:
            unknown.append(name)
    known = {k: v for k, v in cleaned.items() if v is not None}
    if len(known) < MIN_KNOWN_FACTORS or cleaned.get("does_taught_job") is None:
        # The central factor is not optional: a score without it is measuring
        # everything except the question that was asked.
        return None, cleaned, unknown
    total_w = sum(WEIGHTS[k] for k in known)
    return round(sum(v * WEIGHTS[k] for k, v in known.items()) / total_w, 3), \
        cleaned, unknown


def assess(dep: Dependency, alt: Alternative,
           taught_job: str = "") -> Optional[AlternativeOpinion]:
    """Ask a model to judge fit from the quotes we already lifted. Never raises.

    Only ever called for an alternative that already carries AUTHORITATIVE claims, so
    the model is shown text we fetched ourselves and nothing else.
    """
    from miw.llm import complete, load_prompt
    from miw.analyse.notes import _neutralise
    from miw.research.nominate import _taught_job

    quotes = [c for c in alt.claims if c.substantiating]
    if not quotes:
        return None
    evidence = "\n".join(f"- {_neutralise(c.quote[:280])}" for c in quotes[:4])
    prompt = load_prompt(
        "taught_job_fit_v1",
        dependency=dep.canonical_name, candidate=alt.name,
        taught_job=taught_job or _taught_job(dep),
        evidence=evidence)
    res = complete(prompt)
    if not res.ok:
        return None
    data = res.json()
    if not isinstance(data, dict):
        return None

    fit, cleaned, unknown = score(data.get("factors") or {})
    named_unknown = [u for u in (data.get("unknown_factors") or [])
                     if isinstance(u, str)]
    return AlternativeOpinion(
        fit_score=fit,
        does_taught_job=(None if fit is None else fit >= FIT_THRESHOLD),
        factors=cleaned,
        unknown_factors=sorted(set(unknown) | set(named_unknown)),
        one_line=str(data.get("one_line") or "")[:200],
        basis_urls=[c.source_url for c in quotes[:4]],
        provider=res.provider, model=res.model, assessed_at=utcnow())


def render(op: Optional[AlternativeOpinion]) -> str:
    """One line for the digest, labelled so it can never read as evidence."""
    if op is None:
        return ""
    who = f"{op.provider}/{op.model}".strip("/") or "model"
    if not op.assessed:
        return (f"_Model opinion (not evidence · {who}): not assessed - the "
                f"candidate's own pages did not say enough._")
    unknown = (f" Unknown: {', '.join(op.unknown_factors)}."
               if op.unknown_factors else "")
    return (f"_Model opinion (not evidence · {who} · {op.assessed_at[:10]}): "
            f"fit {op.fit_score:.2f}"
            f"{' - likely does the taught job' if op.does_taught_job else ''}"
            f"{' - ' + op.one_line if op.one_line else ''}{unknown}_")
