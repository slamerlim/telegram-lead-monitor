FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY services ./services
COPY shared ./shared
COPY config ./config
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

RUN pip install --upgrade pip \
    && pip install .

CMD ["python", "-m", "services.api.app.main"]
