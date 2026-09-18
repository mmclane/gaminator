FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev \
 && useradd -m -u 1000 bot && mkdir -p /data && chown -R bot:bot /data /app
ENV PATH="/app/.venv/bin:$PATH"
USER bot
VOLUME /data
CMD ["python", "-m", "gaminator"]
