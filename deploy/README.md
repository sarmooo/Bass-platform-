# Deploy

Deployment artifacts for the Bass API. All are validated in CI by
[`.github/workflows/deploy-validate.yml`](../.github/workflows/deploy-validate.yml)
(helm lint + template + kubeconform, and `docker compose config`).

| Artifact | What it is |
|----------|-----------|
| [`docker-compose.yml`](docker-compose.yml) | Single-host stack: Postgres + Redis + API, migrations applied first. |
| [`helm/bass`](helm/bass) | Kubernetes chart: Deployment (non-root, read-only rootfs), Service, optional Ingress + HPA, and a **pre-upgrade migration Job**. |

The operational procedures — deploy, migrate, roll back, scale, incident
response, backup/restore — are in the [operations runbook](../docs/10-operations.md).

## Quick start (single host)

```bash
export BASS_JWT_SECRET=$(openssl rand -hex 32)
docker compose -f deploy/docker-compose.yml up --build -d
curl -sf http://localhost:8000/healthz     # then open http://localhost:8000/
```

## Quick start (Kubernetes)

```bash
helm upgrade --install bass deploy/helm/bass \
  --namespace bass --create-namespace \
  --set image.tag=<sha> \
  --set secret.existingSecret=bass-prod-secret     # keys: jwtSecret, databaseUrl
```

Config surface (env, read by `bass.api:build_default_app`):

| Env | Meaning | Default |
|-----|---------|---------|
| `BASS_JWT_SECRET` | HS256 signing secret (required) | — |
| `BASS_DB_URL` | Postgres DSN; falls back to `BASS_DB_PATH` (SQLite) | `bass.db` |
| `BASS_RATE_LIMIT_PER_MIN` | Per-tenant request cap per 60s window | `1000` |
| `BASS_LOG_JSON` | Structured JSON logs | `1` (image) |
