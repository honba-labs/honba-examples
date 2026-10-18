.PHONY: install test lint test-all

VENV_BIN := .venv/bin

# This repo is developed with uv against a local .venv (honba and honba-data are sibling
# checkouts, not published packages, so `uv run`'s project-wide dependency resolution doesn't
# apply here). Swap `uv pip install -e` for plain `pip install -e` if your environment doesn't
# have uv. test/lint call $(VENV_BIN) explicitly rather than a bare `pytest`/`ruff`, since those
# names may resolve to an unrelated global install on PATH instead of this project's venv.
install:
	uv pip install -e ".[dev]" --python $(VENV_BIN)/python

test:
	$(VENV_BIN)/pytest -q

lint:
	$(VENV_BIN)/ruff check . && $(VENV_BIN)/ruff format --check .

test-all: lint test
