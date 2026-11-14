PY ?= python3

.PHONY: test demo xcheck trajectories

test:
	$(PY) -m unittest discover -s evals/tests -t .
	$(PY) -m unittest discover -s tests -t .

trajectories:
	$(PY) scripts/generate_trajectories.py --check
	$(PY) scripts/validate_trajectory_dataset.py data/trajectories
	$(PY) scripts/audit_trajectory_dataset.py data/trajectories/zh-Hant --manifest data/manifests/trajectories-zh-Hant.json

demo:
	$(PY) -m evals.run --tasks data/samples/tasks.jsonl --tools data/samples/tools.json \
		--runs data/samples/runs/demo-handwritten.jsonl --out results/demo.json --markdown results/demo.md

xcheck:
	$(PY) -m evals.adapters.auditkit_xcheck --tasks data/samples/tasks.jsonl --tools data/samples/tools.json \
		--runs data/samples/runs/demo-handwritten.jsonl
