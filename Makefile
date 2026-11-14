PY ?= python3

.PHONY: test demo xcheck

test:
	$(PY) -m unittest discover -s evals/tests -t .

demo:
	$(PY) -m evals.run --tasks data/samples/tasks.jsonl --tools data/samples/tools.json \
		--runs data/samples/runs/demo-handwritten.jsonl --out results/demo.json --markdown results/demo.md

xcheck:
	$(PY) -m evals.adapters.auditkit_xcheck --tasks data/samples/tasks.jsonl --tools data/samples/tools.json \
		--runs data/samples/runs/demo-handwritten.jsonl
