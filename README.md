# Condenser CORE (collaps-C)

Motor asíncrono de análisis de datos COLLAPS — FastAPI + PostgreSQL + n8n.

## Requisitos

- Python 3.10+
- PostgreSQL accesible desde el runtime
- Variables de entorno: `DATABASE_URL` (obligatoria en producción)

## Desarrollo local

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080
```

## Despliegue en Google Cloud Run

### CPU sin throttling (obligatorio)

Cloud Run, por defecto, **estrangula la CPU** una vez que la respuesta HTTP termina (incluido el `202 Accepted` de jobs en background). Eso degrada severamente el procesamiento asíncrono de chunks (Pandas/Polars + SQL).

**Es obligatorio** desplegar con `--no-cpu-throttling` (o `cpu-throttling: false` en YAML):

```bash
gcloud run deploy bttf-engine \
  --image=REGION-docker.pkg.dev/PROJECT/REPO/bttf-engine:latest \
  --region=us-central1 \
  --memory=2Gi \
  --cpu=2 \
  --concurrency=2 \
  --timeout=900 \
  --no-cpu-throttling \
  --set-env-vars="DATABASE_URL=postgresql://...,SQL_CHUNK_SIZE=10000,DB_POOL_CPU_COUNT=2"
```

Sin este flag, los jobs encolados compiten por CPU residual y los tiempos por chunk se disparan bajo carga.

### PostgreSQL: IP pública vs VPC

- Si Cloud Run y PostgreSQL están en la **misma VPC** (o usas **Cloud SQL Auth Proxy / Private IP**), configure `DATABASE_URL` con el **host privado** o el socket del proxy — no la IP pública.
- Si usa IP pública, asegúrese de **autorizar solo los rangos de egress** de Cloud Run (Serverless VPC Access o NAT estático) en las reglas de firewall de PostgreSQL.
- Un `DATABASE_URL` apuntando a una IP incorrecta o no autorizada produce timeouts de pool (`pool_timeout`) bajo estrés, no errores de aplicación claros.

### Variables de entorno recomendadas

| Variable | Descripción |
|----------|-------------|
| `DATABASE_URL` | Conexión PostgreSQL (`postgresql://`) |
| `SQL_CHUNK_SIZE` | Filas por chunk de lectura (default `10000`) |
| `DB_POOL_CPU_COUNT` | vCPUs del servicio para calcular el pool SQL |
| `DB_POOL_DISK_COUNT` | Discos para fórmula del pool (default `1`) |

## API principal

- `POST /api/v1/condenser/job` — Encola análisis COLLAPS (202 Accepted)
- `POST /api/v1/worktables/create` — Encola materialización de worktable

## Tests

```bash
python -m pytest tests/ -v
```
