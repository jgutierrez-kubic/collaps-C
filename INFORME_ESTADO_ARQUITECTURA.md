# Informe de Estado y Arquitectura Base — Suite COLLAPS

**Fecha:** 2026-07-27  
**Alcance:** Auditoría de código (sin documentación GitBook final)  
**Repositorios auditados:**

| Nombre local | Rol | Stack |
|---|---|---|
| `C:\collaps-C` | Motor matemático (`collaps_engine`) + Orquestador BTTF (`AnalysisEngine` / FastAPI) | Python 3.10, FastAPI, SQLAlchemy, Pandas, Cloud Run |
| `C:\collaps-n8n-nodes` | Nodos custom n8n que construyen el payload y llaman al engine | TypeScript, n8n-workflow, `pg`, Docker/`cloudbuild` |

**Estado Git actual:** Ninguno de los dos directorios es un repositorio Git (`git init` no ejecutado). No hay `.gitignore`. Existe un `.env` con secretos reales en `collaps-C` — **no debe versionarse**.

---

## 0. Resumen ejecutivo

El sistema operativo de análisis COLLAPS sigue este flujo principal:

```text
n8n (nodos Collaps*)
  → POST JSON a Cloud Run
    → FastAPI `/api/v1/condenser/job` (202 Accepted, BackgroundTask)
      → AnalysisEngine
        → PostgreSQL (FULL OUTER JOIN)
        → collaps_engine.execute_transformation (por fila / par de columnas)
        → PostgreSQL append + auto-migración
        → Directus Collections API (auto-registro)
        → callback HTTP opcional a n8n (`$execution.resumeUrl`)
```

**Hallazgo crítico de conteo:** El catálogo oficial en `OPERATIONS_REGISTRY` contiene **21 métodos**, no 22. Además existen **2 alias legacy** (`DIFERENCIA`, `IGUALDAD`) usados por n8n y el validador del payload. El selector de n8n expone **23 opciones** (21 + 2 legacy).

**Deuda técnica relevante:**

1. `app/core/bttf_engine.py` (`CondenserEngine`) y `static/index.html` aún modelan un payload modular antiguo (`JobPayload` / `module_00_on`…), pero `JobPayload` **no existe** en `app/models/payload.py`. El endpoint activo usa `AnalysisPayload` + `AnalysisEngine`.
2. URL del engine hardcodeada en el nodo n8n (`CollapsBttfTrigger`).
3. Sin `.gitignore` ni historial Git en ambos repos.
4. Credenciales de base en `.env` local.

---

## Fase 1 — Análisis del Motor Matemático (`collaps_engine`)

### 1.1 Ubicación y API pública

| Artefacto | Ruta |
|---|---|
| Punto de entrada | `collaps_engine/__init__.py` → `execute_transformation` |
| Orquestador de resultados | `collaps_engine/transformer.py` |
| Registro de operaciones | `collaps_engine/comparison_engine.py` → `OPERATIONS_REGISTRY` |
| Parser de fechas | `collaps_engine/datetime_parser.py` |
| Docs internas existentes | `ENGINE_DOCS.md` |
| Tests | `tests/test_engine.py` |

**Firma canónica:**

```python
from collaps_engine import execute_transformation

result = execute_transformation(val_a, val_b, method_id, options={})
```

**Respuesta estándar:**

| Campo | Tipo | Descripción |
|---|---|---|
| `method_id` | `str` | Método ejecutado |
| `result_value` | `Any` | Resultado (número, bool, dict, list, `None`) |
| `is_match` | `bool \| null` | Inferido cuando aplica (umbrales, tolerancia, bools) |
| `metadata` | `dict` | Incluye `options` usadas |
| `error` | `str \| null` | Error de ejecución o método no registrado |

Parámetros de entrada comunes a todos los métodos:

| Parámetro | Tipo | Rol |
|---|---|---|
| `val_a` | `Any` | Valor de la columna A (fila actual) |
| `val_b` | `Any` | Valor de la columna B (fila actual) |
| `method_id` | `str` | Clave en `OPERATIONS_REGISTRY` |
| `options` | `dict \| None` | Opciones específicas del método |

---

### 1.2 Catálogo de métodos (21 oficiales)

#### A. Numéricas y tolerancia (6)

| # | `method_id` | Propósito | Options | Retorna (`result_value`) |
|---|---|---|---|---|
| 1 | `math_add` | Suma `a + b` | — | `float \| null` |
| 2 | `math_sub` | Resta `a - b` | — | `float \| null` |
| 3 | `math_diff_abs` | Diferencia absoluta `\|a - b\|` | — | `float \| null` |
| 4 | `math_diff_pct` | Diferencia % `((a - b) / a) * 100` | — | `float \| null \| inf` (si `a=0` y `b≠0`) |
| 5 | `math_tolerance` | ¿Dentro de margen? | `epsilon`, `tolerance_pct` | `dict`: `is_within_tolerance`, `delta_abs`, `delta_pct` → `is_match` |
| 6 | `math_ratio` | Ratio `a / b` | — | `float \| null` (null si `b=0`) |

#### B. Texto y cadenas (6)

| # | `method_id` | Propósito | Options | Retorna |
|---|---|---|---|---|
| 7 | `strict_equal` | Igualdad estricta `a == b` | — | `bool` (`is_match`) |
| 8 | `normalized_equal` | Igualdad tras trim, lower y sin acentos | — | `bool` |
| 9 | `fuzzy_levenshtein` | Similitud Levenshtein normalizada `[0,1]` | `threshold` (default `0.85` para `is_match`) | `float` |
| 10 | `fuzzy_jaro_winkler` | Similitud Jaro-Winkler `[0,1]` | `threshold` (default `0.85`) | `float` |
| 11 | `contains_check` | Subcadena: `a in b` o `b in a` | — | `bool` |
| 12 | `regex_match` | Regex sobre `val_a` | `pattern` (o usa `val_b`), `ignore_case` | `bool` |

#### C. Fechas y datetimes (4)

| # | `method_id` | Propósito | Options | Retorna |
|---|---|---|---|---|
| 13 | `date_diff_seconds` | Δ absoluto en segundos (UTC) | — | `float` |
| 14 | `date_diff_days` | Δ absoluto en días | — | `float` |
| 15 | `date_equal` | Mismo día calendario (ignora hora) | — | `bool` |
| 16 | `date_tolerance` | ¿Dentro de margen temporal? | `tolerance_seconds` | `dict`: `is_within_tolerance`, `delta_seconds` → `is_match` |

#### D. Listas y arreglos (3)

| # | `method_id` | Propósito | Options | Retorna |
|---|---|---|---|---|
| 17 | `array_intersection` | Elementos comunes A ∩ B | — | `list` |
| 18 | `array_difference` | Elementos en A no en B | — | `list` |
| 19 | `array_jaccard` | Índice Jaccard `|A∩B|/|A∪B|` | `threshold` (default `0.85` para `is_match`) | `float` |

#### E. Lógica y estructura (2)

| # | `method_id` | Propósito | Options | Retorna |
|---|---|---|---|---|
| 20 | `null_check` | Presencia/ausencia de nulos | — | `dict`: `a_is_null`, `b_is_null`, `both_null`, `any_null` (`is_match = not any_null`) |
| 21 | `boolean_logic` | AND / OR / XOR | `operator`: `"AND"` \| `"OR"` \| `"XOR"` | `bool` |

---

### 1.3 Alias legacy (no son métodos del registry)

Usados por `AnalysisEngine` y validados en `AnalysisPayload`:

| Alias | Mapea a | Nota |
|---|---|---|
| `DIFERENCIA` | `math_sub` con **swap** de operandos (`b - a`) | Compatibilidad COLLAPS legacy |
| `IGUALDAD` | `strict_equal` | Sin swap |

El nodo n8n `bttfMethods.ts` los lista junto a los 21 oficiales (total UI = 23).

---

## Fase 2 — Análisis del bttf-engine (Orquestador)

### 2.1 Identidad del servicio

- **Nombre FastAPI:** `Condenser CORE` (`main.py`)
- **Versión:** `0.1.0`
- **Despliegue observado:** Google Cloud Run  
  `https://bttf-engine-31997537275.us-central1.run.app`
- **Contenedor:** `Dockerfile` → `python:3.10-slim` + `uvicorn main:app --port ${PORT}` (default 8080)
- **Motor activo de jobs:** `AnalysisEngine` (`app/core/analysis_engine.py`)
- **Motor legacy / huérfano:** `CondenserEngine` (`app/core/bttf_engine.py`) — no cableado al router actual

### 2.2 Endpoints expuestos (interacción con n8n)

Prefijo del router: `/api/v1/condenser`

| Método | Ruta completa | Rol | Consumidor |
|---|---|---|---|
| `POST` | `/api/v1/condenser/job` | Encola análisis asíncrono (`HTTP 202`) | **n8n** vía `CollapsBttfTrigger` (URL hardcodeada a Cloud Run) |
| `POST` | `/api/v1/condenser/upload` | Sube archivo auxiliar a GCS (fallback local) | Uso auxiliar / UI |
| `GET` | `/` | Redirect a UI estática `/app` | Operadores / debug |
| Static | `/app` | UI `static/index.html` | **Desalineada** con el contrato actual del job |

**Respuesta 202 de `/job`:**

```json
{
  "status": "accepted",
  "job_id": "<uuid>",
  "analysis_id": "<opcional>",
  "message": "Análisis encolado exitosamente"
}
```

El procesamiento real corre en `BackgroundTasks` → `AnalysisEngine.run(job_id)`.

---

### 2.3 Mapa de flujo de datos (n8n → Python → PostgreSQL / Directus)

```mermaid
sequenceDiagram
  participant N8N as n8n Collaps nodes
  participant CR as Cloud Run FastAPI
  participant AE as AnalysisEngine
  participant CE as collaps_engine
  participant PG as PostgreSQL
  participant DX as Directus

  N8N->>N8N: Mapper + MethodConfigurator arman bttfPayload
  N8N->>CR: POST /api/v1/condenser/job (AnalysisPayload)
  CR-->>N8N: 202 Accepted + job_id
  CR->>AE: BackgroundTask.run(job_id)
  AE->>PG: FULL OUTER JOIN (query_builder)
  PG-->>AE: DataFrame base (_a/_b, estado_cruce)
  AE->>CE: execute_transformation por fila/par
  CE-->>AE: result_value + is_match
  AE->>PG: append + auto-migrate + id SERIAL PK
  AE->>DX: POST /collections (si hay credenciales en portal_projects)
  AE->>N8N: POST callback_url (success|failed + summary)
```

#### Paso a paso

1. **Construcción del payload en n8n**
   - Cadena típica de nodos:
     - `CollapsDbConnection` → conexión Postgres
     - `CollapsSchemaFetcher` / `CollapsTableSelector` / `CollapsColumnSelector` → descubrimiento
     - `CollapsKeyColumnMapper` → emite `bttfPayload` (estructura)
     - `CollapsMethodConfigurator` → emite `metodos_calculo`
     - `CollapsBttfTrigger` → merge + `POST` al engine
   - `bttfPayload` base (Mapper):

     ```json
     {
       "source": "n8n",
       "analysis_id": "n8n_<timestamp>",
       "schema_name": "<schema>",
       "tabla_a": "<table>",
       "tabla_b": "<table>",
       "llave_cruce_a": "<col>",
       "llave_cruce_b": "<col>",
       "columnas_a": "col1,col2",
       "columnas_b": "col1,col2"
     }
     ```

   - Trigger añade: `metodos_calculo`, `nombre_analisis`, `tabla_destino`, y opcionalmente `callback_url` (`$execution.resumeUrl`).

2. **Validación FastAPI**
   - Modelo estricto `AnalysisPayload` (`extra="forbid"`).
   - Valida identificadores SQL y métodos contra `OPERATIONS_REGISTRY ∪ {DIFERENCIA, IGUALDAD}`.
   - Exige misma cardinalidad lógica entre `columnas_a`, `columnas_b` y `metodos_calculo` (validado al construir SQL).

3. **Consulta PostgreSQL**
   - `build_analysis_sql()` genera `FULL OUTER JOIN` entre `schema.tabla_a` y `schema.tabla_b`.
   - Produce columnas: `llave_cruce`, `*_a`, `*_b`, `estado_cruce` (`Match` | `Only A` | `Only B`).
   - Los cálculos **no** se hacen en SQL; solo el cruce.

4. **Llamada al motor matemático**
   - `_apply_collaps_transformations()` itera filas y pares.
   - Resuelve alias legacy y llama `execute_transformation(val_a, val_b, method_id)`.
   - Nombres de columnas resultado:
     - Mismo nombre sanitizado: `{col}__{method}`
     - Distintos: `{col_a}__vs__{col_b}__{method}`
     - Match auxiliar (si aplica): `is_match__...`

5. **Persistencia PostgreSQL**
   - Append histórico (`if_exists="append"`).
   - Metadata: `run_id`, `created_at`, `analysis_id`, `nombre_analisis`, `source`.
   - Auto-migración: `ALTER TABLE ... ADD COLUMN` para columnas nuevas.
   - Añade `id SERIAL PRIMARY KEY` si falta (compatibilidad Directus).

6. **Integración Directus**
   - Lee credenciales desde `public.portal_projects` filtrando por `"Schema_Name"`:
     - `directus_url`
     - `Instance_Token`
   - `POST {directus_url}/collections` con `Authorization: Bearer <token>`.
   - Si la colección ya existe (HTTP 400 / INVALID_PAYLOAD), omite sin fallar el job.

7. **Callback a n8n**
   - Si hay `callback_url` HTTP(S):
     ```json
     {
       "status": "success|failed",
       "analysis_id": "...",
       "schema": "...",
       "summary": {
         "total_rows": 0,
         "matches": 0,
         "only_a": 0,
         "only_b": 0,
         "has_duplicates": false
       }
     }
     ```

---

### 2.4 Contrato del payload (`AnalysisPayload`)

| Campo | Requerido | Tipo / valores | Notas |
|---|---|---|---|
| `source` | no | `"directus"` \| `"n8n"` | Default `"directus"`; n8n envía `"n8n"` |
| `analysis_id` | no | `string` | Trazabilidad |
| `schema_name` | no* | identificador SQL | Default `s00001_incancer` si vacío |
| `nombre_analisis` | no | `string` | UI del Trigger |
| `tabla_a` / `tabla_b` | sí | nombre de tabla | Se sanitiza si viene `schema.tabla` |
| `llave_cruce_a` / `llave_cruce_b` | sí | columna | Identificador SQL |
| `columnas_a` / `columnas_b` | sí | CSV | Mismo nº de elementos que métodos |
| `metodos_calculo` | sí | CSV de method_ids / aliases | Validado |
| `tabla_destino` | sí | tabla destino | Persistencia append |
| `callback_url` | no | URL HTTP(S) | Resume n8n |

\* Tiene default, pero en producción n8n siempre debería enviarlo.

---

### 2.5 Variables de entorno críticas

Definidas / usadas en `app/core/config.py` y runtime:

| Variable | Crítica | Uso | Default |
|---|---|---|---|
| `DATABASE_URL` | **Sí** | Conexión PostgreSQL (SQLAlchemy/psycopg2). Sin ella el análisis falla (o cae a CSV en caminos legacy). | *(ninguno)* |
| `GCS_BUCKET_NAME` | Media | Bucket para `/upload` | `bim-saas-storage-collaps-prod` |
| `PORT` | Media (Cloud Run) | Puerto uvicorn | `8080` |
| `GOOGLE_APPLICATION_CREDENTIALS` / ADC | Media | Auth implícita GCS en Cloud Run / local | ADC del entorno |
| Credenciales Directus | Derivadas | **No** van en `.env` del engine; se leen de `public.portal_projects` | — |

**Formato esperado de `DATABASE_URL`:**

```text
postgresql://USER:PASSWORD@HOST:5432/DATABASE
```

(`postgres://` se normaliza automáticamente a `postgresql://`).

**Advertencia de seguridad:** En el workspace existe `collaps-C/.env` con una URL real. Debe quedar fuera de Git (secrets en Cloud Run / Secret Manager).

---

### 2.6 Inventario de nodos n8n (`collaps-n8n-nodes`)

| Nodo | Función en el flujo |
|---|---|
| `CollapsDbConnection` | Credenciales / conexión Postgres |
| `CollapsSchemaFetcher` | Lista schemas |
| `CollapsTableSelector` | Selección de tablas |
| `CollapsColumnSelector` | Selección de columnas |
| `CollapsDataWatcher` | Observación / metadatos de datos |
| `CollapsKeyColumnMapper` | Arma `bttfPayload` + pares de columnas |
| `CollapsMethodConfigurator` | Asigna métodos (global / per-pair) → `metodos_calculo` |
| `CollapsBttfTrigger` | POST al BTTF Engine en Cloud Run |

Despliegue n8n: `Dockerfile` basado en `n8nio/n8n:2.31.4` + `cloudbuild.yaml` → Artifact Registry `us-central1-docker.pkg.dev/collaps-prod/n8n-repo/n8n-collaps:latest`.

---

## Fase 3 — Propuesta de estructura de documentación (GitBook)

Árbol sugerido compatible con `SUMMARY.md` de GitBook. Cubriría arquitectura, los 21 métodos (+ aliases), consumo webhook y despliegue Cloud Run.

```markdown
# Summary

## Introducción
* [Bienvenida](README.md)
* [Glosario COLLAPS / BTTF](introduccion/glosario.md)
* [Mapa de repositorios](introduccion/repositorios.md)

## Arquitectura general
* [Visión del sistema](arquitectura/vision.md)
* [Flujo n8n → Webhook → Python → PostgreSQL/Directus](arquitectura/flujo-datos.md)
* [Componentes](arquitectura/componentes.md)
  * [collaps_engine (Motor matemático)](arquitectura/componentes/collaps-engine.md)
  * [bttf-engine / Condenser CORE (Orquestador)](arquitectura/componentes/bttf-engine.md)
  * [Nodos n8n Collaps](arquitectura/componentes/nodos-n8n.md)
  * [PostgreSQL y portal_projects](arquitectura/componentes/postgresql.md)
  * [Directus (auto-registro)](arquitectura/componentes/directus.md)
* [Decisiones y deuda técnica conocida](arquitectura/deuda-tecnica.md)

## Referencia técnica — Métodos de transformación
* [Contrato `execute_transformation`](referencia/contrato.md)
* [Índice de métodos](referencia/indice.md)
* [A. Numéricas y tolerancia](referencia/numericas.md)
* [B. Texto y cadenas](referencia/texto.md)
* [C. Fechas y datetimes](referencia/fechas.md)
* [D. Listas y arreglos](referencia/arrays.md)
* [E. Lógica y estructura](referencia/logica.md)
* [Alias legacy (`DIFERENCIA`, `IGUALDAD`)](referencia/alias-legacy.md)
* [Naming de columnas de resultado](referencia/columnas-resultado.md)

## Guía de consumo del Webhook (desde n8n)
* [Endpoint `POST /api/v1/condenser/job`](webhook/endpoint-job.md)
* [Payload esperado (`AnalysisPayload`)](webhook/payload.md)
* [Ejemplos de payloads n8n](webhook/ejemplos.md)
* [Respuesta 202 y ciclo de vida del job](webhook/ciclo-de-vida.md)
* [Callback / resumeUrl](webhook/callback.md)
* [Errores de validación frecuentes](webhook/errores.md)
* [Endpoint auxiliar `POST /upload`](webhook/upload.md)

## Guía de despliegue — Google Cloud Run
* [Prerrequisitos (GCP, Artifact Registry, Secret Manager)](despliegue/prerrequisitos.md)
* [Variables de entorno y secretos](despliegue/variables-entorno.md)
* [Build & Deploy del bttf-engine](despliegue/bttf-engine-cloud-run.md)
* [Build & Deploy de n8n-collaps](despliegue/n8n-collaps.md)
* [Checklist de verificación post-deploy](despliegue/checklist.md)
* [Observabilidad y logs](despliegue/observabilidad.md)

## Operación y desarrollo
* [Setup local](desarrollo/setup-local.md)
* [Ejecutar tests](desarrollo/tests.md)
* [Convenciones de contribución](desarrollo/contribucion.md)
```

Archivo raíz GitBook equivalente:

```text
docs/
├── SUMMARY.md
├── README.md
├── introduccion/
├── arquitectura/
│   └── componentes/
├── referencia/
├── webhook/
├── despliegue/
└── desarrollo/
```

> **Nota:** No se genera aún el contenido final de estas páginas; solo la estructura obligatoria pedida.

---

## Fase 4 — Plan de respaldo en GitHub

### 4.1 Estrategia recomendada

1. **Un repositorio GitHub por carpeta local** (separación de concerns):
   - `collaps-C` → p.ej. `collaps-bttf-engine` (o `collaps-condenser-core`)
   - `collaps-n8n-nodes` → p.ej. `n8n-nodes-collaps`
2. **Nunca** commitear `.env`, virtualenvs, `node_modules`, artefactos `dist/` generados si el CI los construye (o sí versionar `dist/` solo si el flujo Docker actual lo requiere — hoy el `Dockerfile` de n8n hace `COPY dist`).
3. Secretos (`DATABASE_URL`, tokens Directus, credenciales GCP) → **GitHub Secrets / GCP Secret Manager / Cloud Run env**, no archivos.
4. Primer commit limpio: añadir `.gitignore` **antes** de `git add`.
5. Rama principal: `main`.
6. Visibilidad: preferible **private** mientras haya referencias a infra de producción (host DB, project IDs, URLs Cloud Run).

### 4.2 `.gitignore` mínimo recomendado

**Para `collaps-C`:**

```gitignore
# Secrets
.env
.env.*
!.env.example

# Python
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/
dist/
build/

# Runtime / datos locales
data/
outputs/
*.csv
!tests/fixtures/**/*.csv

# IDE / OS
.idea/
.vscode/
.DS_Store
Thumbs.db
```

**Para `collaps-n8n-nodes`:**

```gitignore
# Secrets
.env
.env.*

# Node
node_modules/
npm-debug.log*
yarn-error.log*

# Build (opcional: quitar esta línea si el Docker/CI depende de dist versionado)
# dist/

# IDE / OS
.idea/
.vscode/
.DS_Store
Thumbs.db
```

> Si el pipeline actual de n8n **copia `dist/` desde el contexto de build** sin ejecutar `tsc` en CI, mantén `dist/` versionado o ajusta el `Dockerfile`/`cloudbuild` para compilar antes. Hoy: `COPY dist ./dist` → o se versiona `dist`, o se añade un paso `npm run build`.

Crear también `.env.example` en `collaps-C`:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
GCS_BUCKET_NAME=bim-saas-storage-collaps-prod
PORT=8080
```

### 4.3 Comandos Git exactos — `collaps-C` (bttf-engine)

Ejecutar en PowerShell desde `C:\collaps-C`:

```powershell
# 0) Seguridad: confirmar que .env no se stageará
# (crear .gitignore y .env.example primero)

git init -b main

# Añadir .gitignore y .env.example, luego el código
git add .gitignore .env.example
git add Dockerfile requirements.txt main.py ENGINE_DOCS.md INFORME_ESTADO_ARQUITECTURA.md
git add app/ collaps_engine/ tests/ static/ .dockerignore

# Verificación explícita: .env NO debe aparecer
git status

git commit -m "$(cat <<'EOF'
Initial commit: COLLAPS BTTF engine and mathematical transformer.

EOF
)"

# Crear repo remoto (privado) y push
gh repo create collaps-bttf-engine --private --source=. --remote=origin --push
```

Si `gh` no está autenticado, alternativa:

```powershell
git remote add origin https://github.com/<ORG_O_USER>/collaps-bttf-engine.git
git push -u origin main
```

**Equivalente PowerShell puro** (sin heredoc bash) para el commit:

```powershell
git commit -m "Initial commit: COLLAPS BTTF engine and mathematical transformer."
```

### 4.4 Comandos Git exactos — `collaps-n8n-nodes`

Desde `C:\collaps-n8n-nodes`:

```powershell
git init -b main

git add .gitignore
git add package.json package-lock.json tsconfig.json
git add Dockerfile cloudbuild.yaml
git add nodes/ index.js
# Incluir dist/ solo si el build Docker actual lo requiere sin compilar en CI:
git add dist/

git status   # verificar: node_modules/ NO debe aparecer

git commit -m "Initial commit: COLLAPS custom n8n nodes for BTTF orchestration."

gh repo create n8n-nodes-collaps --private --source=. --remote=origin --push
```

### 4.5 Checklist pre-push (obligatorio)

- [ ] `.env` no está en el staging area
- [ ] No hay passwords/tokens en código o docs (revisar este informe: solo nombres de variables)
- [ ] `node_modules/` y `.venv/` ignorados
- [ ] Repos GitHub en modo **private**
- [ ] Cloud Run / n8n siguen apuntando a secretos externos, no a archivos del repo
- [ ] Considerar rotar la contraseña de DB expuesta en el `.env` local histórico

### 4.6 Qué no hacer

- No usar `git add .` a ciegas antes de tener `.gitignore`.
- No subir `outputs/`, CSVs de prueba con datos de cliente, ni dumps.
- No force-push a `main` tras el primer respaldo compartido.
- No documentar secretos reales en GitBook.

---

## Anexo A — Inventario de archivos clave

### `collaps-C`

```text
main.py                        # FastAPI app
Dockerfile                     # Cloud Run image
requirements.txt
.env                           # SECRETO — no versionar
ENGINE_DOCS.md                 # Docs técnicas del motor (base)
INFORME_ESTADO_ARQUITECTURA.md # Este informe
app/api/endpoints.py           # /job, /upload
app/models/payload.py          # AnalysisPayload
app/core/analysis_engine.py    # Orquestador activo
app/core/bttf_engine.py        # CondenserEngine legacy (huérfano)
app/core/query_builder.py      # SQL FULL OUTER JOIN
app/core/db.py                 # Engine SQLAlchemy
app/core/config.py             # DATABASE_URL, GCS_BUCKET_NAME
app/core/storage_manager.py    # GCS upload
collaps_engine/                # Motor matemático (21 métodos)
tests/                         # pytest
static/index.html              # UI legacy desalineada
```

### `collaps-n8n-nodes`

```text
package.json / tsconfig.json
Dockerfile / cloudbuild.yaml
nodes/CollapsBttfTrigger/      # POST al engine
nodes/CollapsKeyColumnMapper/  # bttfPayload
nodes/CollapsMethodConfigurator/
nodes/helpers/bttfMethods.ts   # Catálogo UI (21 + 2 legacy)
dist/                          # Build TypeScript consumido por Docker
```

---

## Anexo B — Hallazgos abiertos para la siguiente fase documental

1. Alinear conteo oficial: **21 métodos** en código vs expectativa verbal de “22”; decidir si documentar 21 + aliases como “catálogo extendido”.
2. Resolver deuda `CondenserEngine` / `JobPayload` / UI estática vs `AnalysisPayload`.
3. Externalizar `ENGINE_URL` del Trigger (hoy hardcodeada a Cloud Run prod).
4. Definir si `options` por método (threshold, epsilon, regex, etc.) se propagarán desde n8n en una versión futura (hoy el Trigger no envía `options` por par; `execute_transformation` usa defaults).
5. Generar páginas GitBook a partir de este informe + `ENGINE_DOCS.md`.

---

*Fin del Informe de Estado y Arquitectura Base. Próximo paso sugerido: validar nombres de repos GitHub y generar el esqueleto GitBook (`SUMMARY.md` + stubs), sin redactar aún la documentación final.*
