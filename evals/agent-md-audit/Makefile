# Evals of the agent-md-audit plugin. Standard-library Python, no dependencies.
# `make test` is offline: invented traces and a stub agent, no model call, no network.
# `make eval` runs the REAL agent: model calls and web fetches on your own account (see README, "A real run").
.PHONY: test eval

test:
	python3 -m unittest discover -s tests -v

# A real run: make eval ARGS="--only explicit-defects --trials 1"
eval:
	python3 python/run_evals.py $(ARGS)
