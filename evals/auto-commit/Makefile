# Evals of the auto-commit plugin. Standard-library Python, no dependencies.
# `make test` is offline: invented traces and a stub agent that runs real git in temporary repositories, no model call.
# `make eval` runs the REAL agent: model calls on your own account (see README, "A real run").
.PHONY: test eval

test:
	python3 -m unittest discover -s tests -v

# A real run: make eval ARGS="--only wip-preexisting --trials 1"
eval:
	python3 python/run_evals.py $(ARGS)
