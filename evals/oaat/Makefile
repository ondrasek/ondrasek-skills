# Evals of the oaat plugin: the entry point, GNU make. Standard-library Python, portable (Linux and Windows).
# `make run`, `make test` and `make grade-fixtures` work offline: they grade INVENTED traces and call no model.
# `make eval` runs the real agent: one model call per prompt and trial, on your own account (see README, "What a real run costs").
SIDES = python

.PHONY: run test measure grade-fixtures eval compare list

# Every target named on a rule line of this Makefile.
list:
	@awk -F: '/^[^\t #.$$][^:=]*:([^=]|$$)/ { n = split($$1, t, " "); for (i = 1; i <= n; i++) if (t[i] !~ /[%$$]/ && !seen[t[i]]++) print t[i] }' $(MAKEFILE_LIST)

run measure:
	@for d in $(SIDES); do $(MAKE) -s -C $$d $@ || exit 1; done

# Writes captures/grade-after.txt, grade-before.txt, grade-real.txt, compare.txt and judge-prompt.txt.
grade-fixtures:
	@mkdir -p captures
	@$(MAKE) -s -C python grade-fixtures

# A real run on your agent: make eval ARGS="--only explicit-name --trials 1"
eval:
	@$(MAKE) -s -C python eval ARGS="$(ARGS)"

# Before and after a skill edit: make compare BEFORE=artifacts-before/results.json AFTER=artifacts/results.json
compare:
	@$(MAKE) -s -C python compare BEFORE="$(abspath $(BEFORE))" AFTER="$(abspath $(AFTER))"

test:
	python3 -m unittest discover -s tests -v
