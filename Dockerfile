# geo-outreach /scan service for Coolify (Contabo).
# WeasyPrint needs pango/cairo/gdk-pixbuf system libs at runtime.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev \
    libcairo2 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev

# data/ (pipeline.db) lives on a mounted Coolify volume so it survives redeploys.
VOLUME ["/app/data"]
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
