PYTHON ?= python3
VENV ?= .venv
PORT ?= 8777

.PHONY: install test run

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -r requirements.txt

test:
	$(VENV)/bin/python -m pytest tests -q

run:
	$(VENV)/bin/python -m uvicorn app:app --port $(PORT)
