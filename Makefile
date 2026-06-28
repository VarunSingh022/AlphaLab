PYTHON ?= python3.12
PIP := $(PYTHON) -m pip

.PHONY: install install-dev lint format format-check type-check test check clean

install:
	$(PIP) install --upgrade pip
	$(PIP) install -e .

install-dev:
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	$(PYTHON) -m pre_commit install

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

type-check:
	$(PYTHON) -m mypy .

test:
	$(PYTHON) -m pytest

check: lint format-check type-check test

clean:
	rm -rf .mypy_cache .pytest_cache .ruff_cache build dist *.egg-info
