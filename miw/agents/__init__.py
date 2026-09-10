"""MIW's agent graphs.

Two graphs, and the split between them is the whole design:

* `signal_agent` — **acquisition**, which is open-ended and therefore agentic. A model
  chooses which of a vendor's own pages to read; our code fetches them and lifts
  verbatim quotes. It branches, it carries a visited set, and it can resume.
* `impact_agent` — **adjudication**, which is a join over data we already hold and
  therefore has no model in it at all. It is a graph only so the human review gate can
  genuinely suspend.

LangGraph provides control flow. It never provides a fact, and it never calls a model:
nodes go through `miw.llm.complete`, so the provider choice, the 16 denied tools, the
daily call/spend caps and the provider-parity guarantee all still apply inside a graph.
"""
from miw.agents.impact_agent import (build_impact_graph, compile_impact_graph,
                                     run_impact_agent)
from miw.agents.signal_agent import (build_signal_graph, compile_signal_graph,
                                     run_signal_agent)

__all__ = ["build_signal_graph", "compile_signal_graph", "run_signal_agent",
           "build_impact_graph", "compile_impact_graph", "run_impact_agent"]
