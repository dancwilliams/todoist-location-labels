.PHONY: check
check:
	uv run --no-sync ruff format --check .
	uv run --no-sync ruff check .
	uv run --no-sync mypy app.py
	uv run --no-sync pytest
