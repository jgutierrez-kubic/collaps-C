"""Genera archivos CSV de prueba en data/ para el CondenserEngine."""

from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    df_contrato = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "codigo_partida": ["P001", "P002", "P003"],
            "unidad": ["m2", "m3", "kg"],
            "cantidad_contrato": [10.0, 20.0, 15.0],
        }
    )
    df_modelo = pd.DataFrame(
        {
            "id": [1, 2, 4],
            "codigo_partida": ["P001", "P002", "P004"],
            "unidad": ["m2", "m3", "ud"],
            "cantidad_modelo": [10.0, 25.0, 5.0],
        }
    )

    contrato_path = DATA_DIR / "contrato.csv"
    modelo_path = DATA_DIR / "modelo.csv"

    df_contrato.to_csv(contrato_path, index=False)
    df_modelo.to_csv(modelo_path, index=False)

    print(f"Archivos generados:\n  - {contrato_path}\n  - {modelo_path}")


if __name__ == "__main__":
    main()
