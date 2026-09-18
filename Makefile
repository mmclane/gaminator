# Gaminator: common operations. `docker` may be podman on this machine; compose works the same.
.PHONY: start stop restart rebuild logs status test lint

start:          ## Build the image and start the bot in the background
	docker compose up -d --build

stop:           ## Stop the bot cleanly (keeps the database volume)
	docker compose stop

restart: stop start

rebuild:        ## Force a fresh image build, then start
	docker compose build --no-cache
	docker compose up -d

logs:           ## Follow the bot's logs
	docker compose logs -f bot

status:         ## Container state
	docker compose ps

test:           ## Run the test suite
	uv run pytest -q

lint:           ## Lint and format check
	uv run ruff check .
	uv run ruff format --check .
