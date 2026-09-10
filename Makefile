.PHONY: install install-optional test eval triage ui serve spot-check ingest extract probe research analyse report weekly clean fresh

install:
	pip install -r requirements.txt

# Search discovery and LLM note refinement. Needed before any API key does anything.
install-optional:
	pip install -r requirements.txt -r requirements-optional.txt

test:
	python3 -m pytest tests/ -q

# Scored regression harness. Offline, no API key, so it is cheap to run often.
eval:
	python3 eval/run_eval.py

# Reviewer triage: what makes precision measurable and improvable.
triage:
	python3 main.py triage --list

# Local UI on http://127.0.0.1:8000 — run scoped audits, read findings, triage
ui serve:
	python3 main.py serve

# Example of a scoped on-demand audit from the command line.
spot-check:
	python3 main.py probe --course "Intro to Gen AI" --session 4-6 --tiers critical
	python3 main.py analyse --course "Intro to Gen AI" --session 4-6
	python3 main.py report

ingest extract probe research analyse report verify:
	python3 main.py $@

weekly:
	python3 main.py run-weekly

# Rebuild the inventory and registry from scratch. Use after changing an extractor:
# the registry is bootstrapped from the curriculum, so stale entries survive otherwise.
fresh:
	rm -f registry/tools.yaml
	python3 main.py ingest
	python3 main.py extract

# Drops week-over-week memory: the next run reports everything as new.
clean:
	rm -rf out/* state/*
