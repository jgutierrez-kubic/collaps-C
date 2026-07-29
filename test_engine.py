"""Script de prueba offline del AnalysisEngine y query builder."""

from uuid import uuid4

from app.core.analysis_engine import AnalysisEngine
from app.core.query_builder import build_analysis_sql
from app.models.payload import AnalysisPayload

SAMPLE_PAYLOAD = {
    "source": "directus",
    "analysis_id": "test-analysis-001",
    "schema_name": "s00001_incancer",
    "analysisName": "Contract vs model cross-check",
    "tableA": "contrato_itemizado",
    "tableB": "modelo_itemizado",
    "joinKeyA": "id",
    "joinKeyB": "id",
    "columnsA": "cantidad",
    "columnsB": "cantidad",
    "calculationMethods": "DIFERENCIA",
    "targetTable": "c_resultados_itemizado",
    "callbackUrl": "https://httpbin.org/post",
}


def main() -> None:
    payload = AnalysisPayload.model_validate(SAMPLE_PAYLOAD)
    sql = build_analysis_sql(payload)

    print("=== SQL generado ===")
    print(sql)
    print()

    job_id = str(uuid4())
    engine = AnalysisEngine(payload)
    result = engine.run(job_id=job_id)

    if result is None:
        print(f"Job {job_id} no retornó DataFrame (revisa logs / DATABASE_URL).")
        return

    print(f"=== Resultado (job_id={job_id}) ===")
    print(result.to_string(index=False))
    print()
    print(f"Filas: {len(result)} | Columnas: {list(result.columns)}")


if __name__ == "__main__":
    main()
