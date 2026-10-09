PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
PYTEST_ARGS ?= -q
BUILD_ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help install build test lint clean

help:
	@printf '%s\n' \
	  'install  Create a virtualenv and install development dependencies' \
	  'build    Build the source archive and wheel' \
	  'test     Run pytest (override PYTEST_ARGS to select tests)' \
	  'lint     Run Ruff checks' \
	  'clean    Remove build and test artifacts, keeping the virtualenv'

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)

$(VENV)/.installed: pyproject.toml | $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install -e '.[dev]'
	touch $@

install: $(VENV)/.installed

build: install
	$(VENV_PYTHON) -m build $(BUILD_ARGS)

test: install
	$(VENV_PYTHON) -m pytest $(PYTEST_ARGS)

lint: install
	$(VENV_PYTHON) -m ruff check spqrmigrate tests

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache spqrmigrate/__pycache__ tests/__pycache__
