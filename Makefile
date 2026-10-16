.PHONY: ui test lint check cov
ui:
	./tools/ui.sh

test:
	uv run pytest

lint:
	uv run ruff check src tests tools

check:
	./tools/check.sh

cov:
	uv run pytest --cov=pm --cov-report=term-missing
