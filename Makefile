.PHONY: install test lint run docker-build

install:
	python -m pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check .
	ruff format --check .

run:
	uvicorn agenomic_agents.api:app --reload --port 8080

docker-build:
	docker build -t agenomic-demo-agents:local .

