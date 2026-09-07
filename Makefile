PY := uv run

.PHONY: lint typecheck test gate

lint:
	$(PY) ruff check .
	$(PY) ruff format --check .

typecheck:
	$(PY) mypy

test:
	$(PY) pytest

gate: lint typecheck test
