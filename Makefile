.PHONY: check test migrate run seed
check:
	python -m ruff check app tests migrations
	python -m ruff format --check app tests migrations
	python -m mypy app

test:
	python -m pytest -q

migrate:
	python -m alembic upgrade head

run:
	python -m app.main

seed:
	python -m app.cli seed
