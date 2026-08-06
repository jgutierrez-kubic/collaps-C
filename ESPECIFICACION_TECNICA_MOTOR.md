# Especificaciones Técnicas — Condenser CORE (collaps-C)

**Versión:** `0.1.0` (estable post-QA)  
**Stack:** Python 3.10+, FastAPI, PostgreSQL, SQLAlchemy, Pandas, Polars, `collaps_engine`, n8n  
**Despliegue:** Google Cloud Run (`bttf-engine`)  
**Última revisión:** Agosto 2026

---

## 1. Propósito del sistema

Motor asíncrono de **análisis de cruce de datos** (COLLAPS). Compara dos tablas PostgreSQL mediante `FULL OUTER JOIN`, aplica transformaciones matemáticas/lógicas por pares de columnas, persiste resultados en una tabla destino y notifica a n8n vía webhook.

La orquestación externa corre en **n8n**, que construye el payload y consume el callback. No hay integración Directus en el motor; n8n usa el flag `updateSchema` para refrescar colecciones en Directus/NocoDB cuando corresponda.

---

## 2. Arquitectura general

```
n8n (workflow)
  └─ POST /api/v1/condenser/job  →  202 Accepted + jobId
        └─ FastAPI BackgroundTask
              └─ AnalysisEngine.run()
                    ├─ QueryBuilder → SQL FULL OUTER JOIN
                    ├─ Lectura chunked PostgreSQL (LIMIT/OFFSET)
                    ├─ Polars (vectorizado) + collaps_engine (UDF)
                    ├─ Persistencia PostgreSQL (auto-migrate + to_sql)
                    └─ Webhook callback → n8n (con retry/backoff)
```

### Principio de fases (base de datos)

Cada operación de base de datos usa **conexiones cortas**. No se mantiene sesión SQL abierta durante cómputo Polars/Pandas ni durante el envío del webhook.

```
[DB read]   connect → LIMIT/OFFSET → disconnect
[Compute]   pl.from_pandas() → Polars → to_pandas()
[DB write]  begin → auto-migrate (chunk 1) → to_sql → commit
[Network]   webhook n8n (post-job, sin DB)
```

---

## 3. Cómo se dispara

### 3.1 Endpoint principal

| Método | Ruta | Respuesta |
|--------|------|-----------|
| `POST` | `/api/v1/condenser/job` | `202 Accepted` inmediato |

El handler:

1. Valida el body contra `AnalysisPayload` (Pydantic, camelCase).
2. Genera un `jobId` (UUID).
3. Encola `AnalysisEngine.run(job_id)` en `BackgroundTasks` de FastAPI.
4. Devuelve sin esperar el procesamiento.

**Disparador típico:** nodo HTTP de n8n que hace POST al servicio Cloud Run.

**Respuesta inmediata:**

```json
{
  "status": "accepted",
  "jobId": "uuid-del-job",
  "analysisId": "id-del-analisis",
  "message": "Analysis job queued successfully"
}
```

### 3.2 Endpoint secundario (worktables)

| Método | Ruta | Estado |
|--------|------|--------|
| `POST` | `/api/v1/worktables/create` | `202 Accepted` — esqueleto funcional |

Materializa tablas agrupadas (`WorktableEngine`). Sin callback n8n implementado aún.

### 3.3 Auxiliar

| Método | Ruta | Uso |
|--------|------|-----|
| `POST` | `/api/v1/condenser/upload` | Subida de archivos a GCS (o fallback local) |

---

## 4. Contrato de entrada (`AnalysisPayload`)

JSON en **camelCase** desde n8n:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `tableA`, `tableB` | string | Tablas origen en PostgreSQL |
| `joinKeyA`, `joinKeyB` | string | Llaves de cruce |
| `columnsA`, `columnsB` | string CSV | Columnas a comparar (una por transformación) |
| `calculationMethods` | string CSV | Métodos `collaps_engine` (21 + 2 legacy) |
| `targetTable` | string | Tabla destino de resultados |
| `schemaName` | string | Esquema PostgreSQL (default `s00001_incancer`) |
| `analysisId`, `analysisName` | string? | Metadatos de trazabilidad |
| `callbackUrl` | string? | Webhook n8n al finalizar |
| `source` | `"n8n"` \| `"directus"` | Metadato; sin efecto en runtime |

**Restricción:** `columnsA`, `columnsB` y `calculationMethods` deben tener la **misma cantidad** de elementos.

**Alias legacy:**

| Legacy | Método real | Notas |
|--------|-------------|-------|
| `DIFERENCIA` | `math_sub` | Operandos invertidos (`val_b - val_a`) |
| `IGUALDAD` | `strict_equal` | — |

### Ejemplo de payload

```json
{
  "source": "n8n",
  "analysisId": "uuid-analisis",
  "schemaName": "s00001_incancer",
  "analysisName": "Precio Frutas Q3",
  "tableA": "contrato",
  "tableB": "modelo",
  "joinKeyA": "id",
  "joinKeyB": "id",
  "columnsA": "cantidad, nombre",
  "columnsB": "cantidad, nombre",
  "calculationMethods": "DIFERENCIA, fuzzy_levenshtein",
  "targetTable": "c_results_precioFrutas",
  "callbackUrl": "https://n8n.example.com/webhook/..."
}
```

---

## 5. Flujo de ejecución (`AnalysisEngine.run`)

### Fase 1 — Preparación

- Reset de `updateSchema = false`, `filas_insertadas = 0`.
- `build_analysis_sql()` genera el `FULL OUTER JOIN` con columnas indexadas.
- `_fetch_source_uniqueness_stats()` audita duplicados en llaves de cruce.
- `log_join_uniqueness_warning()` registra advertencias si hay riesgo de producto cartesiano.

### Fase 2 — Procesamiento por chunks

Tamaño configurable vía `SQL_CHUNK_SIZE` (default **10 000** filas).

Por cada chunk:

1. **Lectura:** `LIMIT/OFFSET` sobre subquery del SQL de análisis; conexión cerrada antes del cómputo.
2. **Transformación:** `pl.from_pandas()` → motor Polars híbrido → `to_pandas()`.
3. **Persistencia:** transacción única con auto-migrate (si aplica) + `to_sql`.

**`run_id`:** incremental por tabla destino (`MAX(run_id) + 1`), compartido en todos los chunks del mismo job.

| Chunk | Tabla destino | Modo `to_sql` |
|-------|---------------|---------------|
| 1 (tabla nueva) | No existe | `replace` (CREATE) |
| 1 (tabla existe) | Existe | `append` + migrate |
| 2..N | — | `append` |

### Fase 3 — Callback (sin DB abierta)

- POST a `callbackUrl` con retry exponencial (tenacity, **5 intentos**).
- Reintenta en errores de red y HTTP `429, 408, 500, 502, 503, 504`.
- Fallo definitivo del callback **no detiene** el job ni revierte la persistencia.

### Fase 4 — Metadatos en cada fila persistida

Columnas de trazabilidad inyectadas antes de escribir:

- `run_id`
- `created_at`, `timestamp`
- `job_id`
- `analysis_id`, `analysis_name`
- `source`
- `estado_cruce`, `llave_cruce` (del SQL de cruce)

---

## 6. QueryBuilder — SQL de cruce

Genera una consulta del tipo:

```sql
SELECT
  COALESCE(a."llave_a"::text, b."llave_b"::text) AS "llave_cruce",
  a."llave_a" AS "llave_a_a",
  b."llave_b" AS "llave_b_b",
  CASE
    WHEN a."llave_a" IS NOT NULL AND b."llave_b" IS NOT NULL THEN 'Match'
    WHEN a."llave_a" IS NOT NULL THEN 'Only A'
    ELSE 'Only B'
  END AS estado_cruce,
  a."val" AS "0_val_a",
  b."val" AS "0_val_b",
  a."val" AS "1_val_a",
  b."val" AS "1_val_b"
FROM "schema"."tabla_a" a
FULL OUTER JOIN "schema"."tabla_b" b
  ON a."llave_a" = b."llave_b"
```

### Alias indexados (fix crítico)

Formato: `{índice}_{columna_sanitizada}_{lado}` → ej. `0_val_a`, `1_val_a`.

Permite mapear la **misma columna de origen más de una vez** sin colisiones de alias SQL (bug que producía DataFrames 2D y rompía la vectorización).

---

## 7. Motor de transformaciones (híbrido Polars + collaps_engine)

### 7.1 `collaps_engine` — 21 métodos + 2 legacy

| Familia | Métodos |
|---------|---------|
| **Numéricos** | `math_add`, `math_sub`, `math_diff_abs`, `math_diff_pct`, `math_tolerance`, `math_ratio` |
| **Texto** | `strict_equal`, `normalized_equal`, `fuzzy_levenshtein`, `fuzzy_jaro_winkler`, `contains_check`, `regex_match` |
| **Fechas** | `date_diff_seconds`, `date_diff_days`, `date_equal`, `date_tolerance` |
| **Arrays** | `array_intersection`, `array_difference`, `array_jaccard` |
| **Lógica** | `null_check`, `boolean_logic` |

Cada transformación devuelve un dict estandarizado:

```json
{
  "method_id": "math_sub",
  "result_value": 5.0,
  "is_match": null,
  "metadata": { "options": {} },
  "error": null
}
```

Ante dirty data (JSONs rotos, divisiones por cero, tipos inválidos), `error` se popula y el job **continúa** sin detenerse.

### 7.2 Rutas de ejecución en Polars (`polars_transformer.py`)

| Ruta | Métodos | Implementación |
|------|---------|----------------|
| **Vectorizada (Polars nativo)** | 9 métodos simples | Expresiones Polars (`+`, `-`, `abs()`, comparaciones, casts) |
| **UDF (`map_elements`)** | fuzzy, arrays, regex, tolerancias, fechas complejas | Delega a `execute_transformation()` |

**Métodos vectorizados en Polars:**

`math_add`, `math_sub`, `math_diff_abs`, `math_diff_pct`, `math_ratio`, `strict_equal`, `normalized_equal`, `date_equal`, `boolean_logic`

### 7.3 Columnas de salida (por par índice `i`)

| Columna | Ejemplo |
|---------|---------|
| Valor lado A | `0_cantidadA` |
| Valor lado B | `0_cantidadB` |
| Método aplicado | `0_metodo_aplicado` |
| Resultado | `0_diferencia`, `0_math_add`, `0_fuzzy_levenshtein`, etc. |
| Match (si aplica) | `0_is_match` |

Las columnas SQL crudas (`0_cantidad_a`, `1_cantidad_a`, etc.) se **eliminan** tras transformar.

---

## 8. Persistencia y auto-migración

| Aspecto | Comportamiento |
|---------|----------------|
| **Destino** | `{schemaName}.{targetTable}` en PostgreSQL |
| **Auto-migrate** | `ALTER TABLE ADD COLUMN IF NOT EXISTS` en chunk 1 si la tabla existe pero faltan columnas |
| **CREATE TABLE** | `to_sql(if_exists="replace")` en chunk 1 si la tabla no existe |
| **Historial** | Múltiples ejecuciones sobre la misma tabla → `append` con `run_id` incremental |

### Flag `updateSchema`

| Evento | `updateSchema` |
|--------|----------------|
| Solo INSERT/UPDATE de datos | `false` |
| CREATE TABLE (tabla nueva) | `true` |
| ALTER TABLE ADD COLUMN | `true` |

n8n usa este flag para decidir si refrescar esquema en Directus/NocoDB.

---

## 9. Payload del webhook (callback n8n)

Enviado al finalizar el job (éxito o fallo):

```json
{
  "status": "success",
  "analysisId": "uuid-analisis",
  "schema": "s00001_incancer",
  "targetTable": "c_results_precioFrutas",
  "updateSchema": true,
  "filas_insertadas": 100,
  "jobId": "uuid-job",
  "summary": {
    "totalRows": 100,
    "matches": 80,
    "onlyA": 10,
    "onlyB": 10,
    "hasDuplicates": false
  }
}
```

### Resiliencia del callback

| Parámetro | Valor |
|-----------|-------|
| Intentos máximos | 5 |
| Espera | Exponencial: min 2 s, max 20 s |
| Reintenta en | `httpx.RequestError`, HTTP 429, 408, 500, 502, 503, 504 |

---

## 10. Gestión de base de datos (`db.py`)

| Parámetro | Valor |
|-----------|-------|
| Pool size | `(DB_POOL_CPU_COUNT × 2) + DB_POOL_DISK_COUNT` |
| Max overflow | `0` (fail-fast, sin conexiones extra silenciosas) |
| Pool timeout | `5 s` |
| Pre-ping | `true` |
| Pool recycle | `300 s` |
| Connect timeout | `10 s` |
| Singleton | `get_db_engine()` con `@lru_cache` |

Todos los módulos (`AnalysisEngine`, `bttf_engine`, `WorktableEngine`) comparten el **mismo pool centralizado**.

---

## 11. Variables de entorno

| Variable | Obligatoria | Default | Uso |
|----------|-------------|---------|-----|
| `DATABASE_URL` | Sí (prod) | — | Conexión PostgreSQL (`postgresql://`) |
| `SQL_CHUNK_SIZE` | No | `10000` | Filas por chunk de lectura |
| `DB_POOL_CPU_COUNT` | No | `os.cpu_count()` | Cálculo del pool SQL |
| `DB_POOL_DISK_COUNT` | No | `1` | Cálculo del pool SQL |
| `PORT` | No | `8080` | Puerto HTTP (Cloud Run) |

---

## 12. Despliegue en Google Cloud Run

### CPU sin throttling (obligatorio)

Cloud Run estrangula la CPU tras devolver el `202 Accepted` si no se desactiva. Esto arruina el procesamiento en background.

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

### PostgreSQL: conectividad

- **VPC / Private IP / Cloud SQL Auth Proxy:** usar host privado o socket del proxy en `DATABASE_URL`.
- **IP pública:** autorizar solo rangos de egress de Cloud Run (Serverless VPC Access o NAT estático).
- IP incorrecta o firewall mal configurado → timeouts de pool bajo estrés, no errores de aplicación claros.

### Parámetros recomendados bajo estrés

| Parámetro | Valor recomendado |
|-----------|-------------------|
| Memoria | 2–4 GiB |
| CPU | 2 vCPU |
| Concurrencia por instancia | 1–2 |
| `--no-cpu-throttling` | Obligatorio |

---

## 13. Resiliencia y rendimiento

| Capacidad | Detalle |
|-----------|---------|
| Chunking anti-OOM | Lectura/persistencia por lotes configurables |
| Conexiones cortas | Sin sesión DB durante cómputo o red |
| Polars + pyarrow | Cómputo vectorizado en Rust para métodos simples |
| Dirty data | Errores por fila capturados; job no se detiene |
| Webhook backoff | Reintentos exponenciales ante saturación del destino |
| Detección duplicados | Warning si llaves de cruce no son únicas |
| División por cero | Retorna `null`/`inf` según método; sin crash |

---

## 14. Módulos del repositorio

| Módulo | Rol |
|--------|-----|
| `main.py` | App FastAPI, routers |
| `app/api/endpoints.py` | API condenser (`/job`, `/upload`) |
| `app/api/worktable_endpoints.py` | API worktables (`/create`) |
| `app/core/analysis_engine.py` | Orquestador principal del análisis |
| `app/core/query_builder.py` | Generador SQL de cruce |
| `app/core/polars_transformer.py` | Motor híbrido Polars + UDF |
| `app/core/db.py` | Pool SQLAlchemy centralizado |
| `app/models/payload.py` | Contrato `AnalysisPayload` |
| `collaps_engine/` | Librería de 21 operaciones de comparación |
| `app/core/worktable_engine.py` | Worktables (parcial) |
| `app/core/bttf_engine.py` | Motor legacy BTTF (no expuesto en API activa) |
| `app/core/storage_manager.py` | Upload GCS/local |

---

## 15. Cobertura de tests

**46 tests** (`pytest`):

- Validación de payload camelCase
- Transformaciones Polars (vectorizado + UDF fuzzy)
- Query builder (alias indexados, columnas repetidas)
- `collaps_engine` (21 métodos)
- Persistencia y `updateSchema`
- Worktable payload

```bash
python -m pytest tests/ -v
```

---

## 16. Limitaciones conocidas (v0.1.0)

| Limitación | Estado |
|------------|--------|
| Sin estado intermedio del job | Solo callback final |
| Sin cola externa (Cloud Tasks / Pub/Sub) | Jobs en `BackgroundTasks` del mismo proceso |
| Worktables sin webhook n8n | Pendiente |
| Sin registro automático en Directus | Responsabilidad de n8n vía `updateSchema` |
| `bttf_engine.py` (payload modular antiguo) | Existe pero no cableado al endpoint activo |
| Paginación `LIMIT/OFFSET` | Puede degradar en tablas muy grandes (candidato a keyset pagination) |

---

## 17. Diagrama de secuencia (job completo)

```
n8n                FastAPI              AnalysisEngine           PostgreSQL          n8n (callback)
 │                    │                       │                      │                    │
 │── POST /job ──────>│                       │                      │                    │
 │<── 202 + jobId ────│                       │                      │                    │
 │                    │── BackgroundTask ────>│                      │                    │
 │                    │                       │── stats query ──────>│                    │
 │                    │                       │<── counts ───────────│                    │
 │                    │                       │                      │                    │
 │                    │                       │── LIMIT/OFFSET ─────>│  (por chunk)       │
 │                    │                       │<── chunk ────────────│                    │
 │                    │                       │── Polars transform   │                    │
 │                    │                       │── begin + to_sql ───>│                    │
 │                    │                       │<── commit ───────────│                    │
 │                    │                       │     (repetir)        │                    │
 │                    │                       │                      │                    │
 │                    │                       │── POST callback ──────────────────────────>│
 │                    │                       │<── 200 OK ────────────────────────────────│
```

---

*Documento generado a partir del estado del repositorio en rama `main` (commit `6c81922`).*
