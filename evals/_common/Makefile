# The two modules every eval harness under evals/ imports: python/agent_trace.py (the stream-json reader) and python/runner_cmd.py
# (the command runner). Standard-library Python, no dependencies. All targets are offline: no model call, no network.
HARNESSES = oaat agent-md-audit auto-commit block-force-push milestones sitrep
.PHONY: test layout test-all

# The unit tests of the two modules and the layout check (no harness keeps a local copy of them: tests/test_layout.py).
test:
	python3 -m unittest discover -s tests -v

# Every harness imports the shared modules from here and keeps no copy (see tests/test_layout.py).
layout:
	python3 -m unittest discover -s tests -p 'test_layout.py' -v

# After a change here: the tests above (they include the layout check), then the offline tests of every harness (minutes: three of them run real git).
test-all: test
	@for h in $(HARNESSES); do $(MAKE) -C ../$$h test || exit 1; done
