PYTHON ?= python
PIP ?= pip

.PHONY: install install-dev run test eval lint clean

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"

run:
	$(PYTHON) -m mira

test:
	@if [ -d tests ]; then $(PYTHON) -m pytest tests/ -v; else echo "No tests/ yet (added in Batch 8)."; fi

eval:
	@echo "Evals not yet implemented (Batch 8)."

lint:
	$(PYTHON) -m ruff check src/

clean:
	rm -rf build dist *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
