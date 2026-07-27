# COLLAPS Engine — Documentación Técnica Oficial

Motor de transformación y comparación unificado para la suite COLLAPS.  
Usado en nodos custom de **n8n**, webhooks de **Directus** y el pipeline `analysis_engine`.

---

## Punto de entrada

```python
from collaps_engine import execute_transformation

result = execute_transformation(val_a, val_b, method_id, options={})
```

### Estructura de respuesta estándar

```json
{
  "method_id": "fuzzy_levenshtein",
  "result_value": 0.87,
  "is_match": true,
  "metadata": { "options": { "threshold": 0.85 } },
  "error": null
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `method_id` | `str` | Identificador del método ejecutado |
| `result_value` | `Any` | Valor calculado (número, bool, dict, list) |
| `is_match` | `bool \| null` | Inferido automáticamente cuando aplica |
| `metadata` | `dict` | Opciones usadas en la ejecución |
| `error` | `str \| null` | Mensaje de error si la operación falló |

---

## A. Numéricas y Tolerancia

### `math_add`
Suma aritmética: `a + b`.

- **Options:** ninguna
- **Input:** `val_a=10`, `val_b=5`
- **Output:** `{"result_value": 15.0, "is_match": null}`

### `math_sub`
Resta aritmética: `a - b`.

- **Options:** ninguna
- **Input:** `val_a=10`, `val_b=3`
- **Output:** `{"result_value": 7.0}`

### `math_diff_abs`
Diferencia absoluta: `|a - b|`.

- **Options:** ninguna
- **Input:** `val_a=10`, `val_b=13`
- **Output:** `{"result_value": 3.0}`

### `math_diff_pct`
Diferencia porcentual: `((a - b) / a) * 100`.

- **Options:** ninguna
- **Input:** `val_a=100`, `val_b=80`
- **Output:** `{"result_value": 20.0}`

### `math_tolerance`
Evalúa si dos valores están dentro de un margen.

- **Options:** `{"epsilon": 10}` y/o `{"tolerance_pct": 5}`
- **Input:** `val_a=100`, `val_b=102`, `options={"epsilon": 5}`
- **Output:**
  ```json
  {
    "result_value": {
      "is_within_tolerance": true,
      "delta_abs": 2.0,
      "delta_pct": 2.0
    },
    "is_match": true
  }
  ```

### `math_ratio`
División: `a / b` (retorna `null` si `b=0`).

- **Options:** ninguna
- **Input:** `val_a=10`, `val_b=2`
- **Output:** `{"result_value": 5.0}`

---

## B. Texto y Cadenas

### `strict_equal`
Igualdad estricta `a == b`.

- **Options:** ninguna
- **Input:** `val_a="Hola"`, `val_b="Hola"`
- **Output:** `{"result_value": true, "is_match": true}`

### `normalized_equal`
Igualdad tras trim, lowercase y remoción de acentos.

- **Options:** ninguna
- **Input:** `val_a="Café"`, `val_b="cafe"`
- **Output:** `{"result_value": true, "is_match": true}`

### `fuzzy_levenshtein`
Similitud Levenshtein normalizada (0.0 – 1.0).

- **Options:** `{"threshold": 0.85}` (para `is_match`)
- **Input:** `val_a="kitten"`, `val_b="sitting"`
- **Output:** `{"result_value": 0.57, "is_match": false}`

### `fuzzy_jaro_winkler`
Similitud Jaro-Winkler (0.0 – 1.0).

- **Options:** `{"threshold": 0.85}`
- **Input:** `val_a="martha"`, `val_b="marhta"`
- **Output:** `{"result_value": 0.96, "is_match": true}`

### `contains_check`
Verifica si `a` está contenido en `b` o viceversa.

- **Options:** ninguna
- **Input:** `val_a="world"`, `val_b="hello world"`
- **Output:** `{"result_value": true, "is_match": true}`

### `regex_match`
Valida `val_a` contra una expresión regular.

- **Options:** `{"pattern": "^[A-Z]+$", "ignore_case": false}`
- **Input:** `val_a="ABC123"`, `options={"pattern": "^[A-Z]+"}`
- **Output:** `{"result_value": false}`

---

## C. Fechas y Datetimes

### `date_diff_seconds`
Diferencia absoluta en segundos entre dos fechas (UTC).

- **Options:** ninguna
- **Input:** `val_a="2024-01-01T00:00:00Z"`, `val_b="2024-01-01T00:01:00Z"`
- **Output:** `{"result_value": 60.0}`

### `date_diff_days`
Diferencia absoluta en días.

- **Options:** ninguna
- **Input:** `val_a="2024-01-01"`, `val_b="2024-01-03"`
- **Output:** `{"result_value": 2.0}`

### `date_equal`
Compara si corresponden al mismo día calendario (ignora hora).

- **Options:** ninguna
- **Input:** `val_a="2024-01-01 00:00"`, `val_b="2024-01-01 23:59"`
- **Output:** `{"result_value": true, "is_match": true}`

### `date_tolerance`
Evalúa si dos fechas están dentro de una tolerancia en segundos.

- **Options:** `{"tolerance_seconds": 3600}`
- **Input:** `val_a="2024-01-01T00:00:00Z"`, `val_b="2024-01-01T00:30:00Z"`
- **Output:**
  ```json
  {
    "result_value": {"is_within_tolerance": true, "delta_seconds": 1800.0},
    "is_match": true
  }
  ```

---

## D. Listas y Arreglos

### `array_intersection`
Elementos comunes entre dos arreglos.

- **Options:** ninguna
- **Input:** `val_a=[1,2,3]`, `val_b=[2,3,4]`
- **Output:** `{"result_value": [2, 3]}`

### `array_difference`
Elementos en A que no están en B.

- **Options:** ninguna
- **Input:** `val_a=[1,2,3]`, `val_b=[2]`
- **Output:** `{"result_value": [1, 3]}`

### `array_jaccard`
Índice Jaccard: `|A ∩ B| / |A ∪ B|` (0.0 – 1.0).

- **Options:** `{"threshold": 0.5}`
- **Input:** `val_a=[1,2]`, `val_b=[2,3]`
- **Output:** `{"result_value": 0.333}`

---

## E. Lógica y Estructura

### `null_check`
Detecta presencia/ausencia de nulos en ambas columnas.

- **Options:** ninguna
- **Input:** `val_a=None`, `val_b=1`
- **Output:**
  ```json
  {
    "result_value": {
      "a_is_null": true,
      "b_is_null": false,
      "both_null": false,
      "any_null": true
    },
    "is_match": false
  }
  ```

### `boolean_logic`
Operaciones lógicas AND, OR, XOR.

- **Options:** `{"operator": "AND"}` | `"OR"` | `"XOR"`
- **Input:** `val_a=true`, `val_b=false`, `options={"operator": "XOR"}`
- **Output:** `{"result_value": true, "is_match": true}`

---

## Integración con `analysis_engine`

El payload COLLAPS define métodos por par de columnas:

```json
{
  "columnas_a": "cantidad, nombre",
  "columnas_b": "cantidad, nombre",
  "metodos_calculo": "DIFERENCIA, fuzzy_levenshtein"
}
```

| Alias legacy | Mapeo collaps_engine | Columna generada |
|---|---|---|
| `DIFERENCIA` | `math_sub` (b − a) | `delta_{col_a}` |
| `IGUALDAD` | `strict_equal` | `igualdad_{col_a}` |
| Cualquier `method_id` | directo | `{method_id}_{col_a}` |

Flujo:
1. SQL hace el `FULL OUTER JOIN` y selecciona `{col}_a` / `{col}_b`
2. `collaps_engine.execute_transformation` aplica cada método fila a fila
3. Resultado se persiste en `{schema}.{tabla_destino}` con historial (`append`)

---

## Ejecutar tests

```bash
python -m pytest tests/test_engine.py -v
```
