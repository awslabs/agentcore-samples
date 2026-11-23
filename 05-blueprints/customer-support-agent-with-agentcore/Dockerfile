FROM public.ecr.aws/docker/library/python:3.13-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN useradd -m -u 1000 runtime
USER runtime
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "src.main"]
