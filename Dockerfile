# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.14-slim-bookworm
FROM ${PYTHON_IMAGE} AS wheels
WORKDIR /build
COPY requirements.txt .
RUN python -m pip wheel --no-cache-dir --only-binary=:all: --wheel-dir=/wheels -r requirements.txt

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 dairy \
    && useradd --uid 10001 --gid dairy --create-home dairy
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    python -m pip install --no-index --find-links=/wheels -r requirements.txt
# The build context is an allowlist: local databases, backups and secrets stay out.
COPY --chown=dairy:dairy . .
RUN mkdir -p /app/backups /app/staticfiles \
    && chown dairy:dairy /app/backups /app/staticfiles \
    && chmod 700 /app/backups
USER dairy:dairy
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind=0.0.0.0:8000", "--workers=2", "--threads=2", "--timeout=120", "--access-logfile=-", "--error-logfile=-"]
