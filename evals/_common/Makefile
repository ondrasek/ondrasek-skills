# The two modules every eval harness under evals/ imports: python/agent_trace.py (the stream-json reader) and python/runner_cmd.py
# (the command runner). Standard-library Python, no dependencies. All targets are offline: no model call, no network.
HARNESSES = oaat agent-md-audit auto-commit block-force-push milestones sitrep
.PHONY: test layout test-all

# The unit tests of the two modules. tests/test_layout.py is not in this target while the harnesses still keep local copies
# (it is red until the last one is migrated): run it with `make layout`. When the migration is done, make `test` discover all of tests/.
test:
	python3 -m unittest discover -s tests -p 'test_[ar]*.py' -v

# Every harness imports the shared modules from here and keeps no copy (see tests/test_layout.py).
layout:
	python3 -m unittest discover -s tests -p 'test_layout.py' -v

# After a change here: the tests above, the layout check, then the offline tests of every harness (minutes: three of them run real git).
test-all: test layout
	@for h in $(HARNESSES); do $(MAKE) -C ../$$h test || exit 1; done
