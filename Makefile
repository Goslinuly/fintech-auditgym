.PHONY: install lint test check episodes episodes-check baselines ui static demo docker-build docker-check

install:
	pip install -e ".[dev]"

lint:
	ruff check auditgym tests examples ui

test:
	pytest -q

# Single check command: lint + tests + byte-for-byte episode regeneration (sha256)
check: lint test episodes-check

episodes:
	python -m auditgym.generator build

episodes-check:
	python -m auditgym.generator build --check

ui:
	python ui/server.py

baselines:
	python examples/baselines.py

static:
	python ui/build_static.py --out site

demo:
	python examples/baselines.py --trace

docker-build:
	docker build -t audit-rl-env .

docker-check: docker-build
	docker run --rm --network none --cpus 2 --memory 4g audit-rl-env make check
