# Multi-stage build for the Bass API service.
# Stage 1 builds a wheel; stage 2 is a slim, non-root runtime.

FROM python:3.11-slim AS build
WORKDIR /src
COPY reference/ .
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.11-slim
# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 bass
WORKDIR /app
COPY --from=build /src/dist/*.whl /tmp/
# Install the wheel with the API and PostgreSQL extras.
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[api,postgres]" && rm -rf /tmp/*.whl
USER bass
ENV BASS_DB_PATH=/tmp/bass.db BASS_LOG_JSON=1
EXPOSE 8000
# BASS_JWT_SECRET must be provided at runtime.
CMD ["uvicorn", "--factory", "bass.api:build_default_app", \
     "--host", "0.0.0.0", "--port", "8000"]
