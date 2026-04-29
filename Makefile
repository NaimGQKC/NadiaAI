.PHONY: install test test-integration test-all lint format run run-cron migrate clean

install:
	pip install -e ".[dev]"

test:
	pytest tests/unit -v --cov=nadia_ai --cov-report=term-missing

test-integration:
	pytest tests/unit tests/integration -v --cov=nadia_ai --cov-report=term-missing

test-all:
	pytest -v --cov=nadia_ai --cov-report=term-missing

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

run:
	python -m nadia_ai

run-cron:
	python -m nadia_ai --cron

migrate:
	python -c "from nadia_ai.db import get_connection, init_db; init_db(get_connection())"

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
