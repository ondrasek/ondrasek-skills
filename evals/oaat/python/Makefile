# GNU make; the Python side (portable: the same files run on Linux and Windows).
# run: the demo on the invented fixture traces (offline, no key). grade-fixtures: the same, one file per section in ../captures.
# eval: the real agent (needs `claude`, see ../README.md). compare: two results.json files, before and after a skill edit.
.PHONY: run grade-fixtures eval compare test measure list
run:
	python3 demo.py

grade-fixtures:
	python3 demo.py after > ../captures/grade-after.txt
	python3 demo.py before > ../captures/grade-before.txt
	python3 demo.py real > ../captures/grade-real.txt
	python3 demo.py compare > ../captures/compare.txt
	python3 demo.py judge-prompt > ../captures/judge-prompt.txt

eval:
	python3 run_evals.py $(ARGS)

compare:
	python3 report.py $(BEFORE) $(AFTER)

test:
	python3 -m unittest discover -s ../tests -v

measure:
	python3 measure.py

list:
	@awk -F: '/^[^\t #.$$][^:=]*:([^=]|$$)/ { n = split($$1, t, " "); for (i = 1; i <= n; i++) if (t[i] !~ /[%$$]/ && !seen[t[i]]++) print t[i] }' $(MAKEFILE_LIST)
