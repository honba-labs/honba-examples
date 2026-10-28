.PHONY: install test lint typecheck test-parity test-all

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

typecheck:
	$(VENV_BIN)/pyright

# Asserts the Python backtest returns match the compiled Rust backtest (honba-ya-backtest)
# within 1e-6, using golden vectors in tests/snapshots/. Fully offline; no Rust toolchain
# required on the Python side (Phase 7.1 decoupling).
test-parity:
	$(VENV_BIN)/pytest -q tests/unit/test_backtest_parity.py

test-all: lint typecheck test test-parity
