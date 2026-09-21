"""Genera el Parquet consolidado fuera de Streamlit.

Ejecutar desde la carpeta raíz del proyecto:
    python generar_parquet.py

Opcionalmente:
    python generar_parquet.py --source datos_semanales --output datos_semanales/datos_consolidados.parquet
"""

from __future__ import annotations

import argparse
import gc
import io
from pathlib import Path
from typing import List

import pandas as pd

# Se reutiliza la misma lógica de negocio del dashboard: homologación, pólizas,
# dimensiones y reincidencias. Importar app.py no abre la interfaz porque main()
# sólo se ejecuta cuando Streamlit inicia la aplicación.
from app import calcular_reincidencias_vectorizadas, transformar_dataset_base


def leer_csv(ruta: Path, raiz: Path) -> pd.DataFrame:
    """Lee un CSV con codificación robusta y registra su archivo de origen."""
    ultimo_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        try:
            df = pd.read_csv(
                ruta,
                dtype=str,
                low_memory=False,
                encoding=encoding,
                on_bad_lines="skip",
            )
            if df.empty:
                raise ValueError("no contiene filas")
            df.columns = [str(c).strip() for c in df.columns]
            df["Archivo_Origen"] = ruta.relative_to(raiz.parent).as_posix()
            return df
        except Exception as exc:
            ultimo_error = exc
    raise RuntimeError(f"No se pudo leer {ruta}: {ultimo_error}")


def listar_csv(raiz: Path, salida: Path) -> List[Path]:
    """Incluye la carpeta archivo/ y excluye únicamente el archivo de salida."""
    salida_abs = salida.resolve()
    archivos = [
        ruta for ruta in raiz.rglob("*")
        if ruta.is_file() and ruta.suffix.lower() == ".csv" and ruta.resolve() != salida_abs
    ]
    # Si hay un duplicado temporal, se procesa primero el archivado y después
    # el de la carpeta principal, que normalmente representa la versión vigente.
    return sorted(archivos, key=lambda p: ("/archivo/" not in p.as_posix(), p.as_posix()))


def construir_parquet(raiz: Path, salida: Path) -> None:
    archivos = listar_csv(raiz, salida)
    if not archivos:
        raise FileNotFoundError(f"No hay CSV para consolidar dentro de: {raiz}")

    print(f"CSV detectados: {len(archivos)}")
    frames: List[pd.DataFrame] = []
    errores: List[str] = []

    for posicion, ruta in enumerate(archivos, start=1):
        try:
            frame = leer_csv(ruta, raiz)
            frames.append(frame)
            print(f"[{posicion}/{len(archivos)}] {ruta.name}: {len(frame):,} filas")
        except Exception as exc:
            errores.append(str(exc))

    # No se publica un consolidado parcial: primero deben ser legibles todos
    # los archivos históricos.
    if errores:
        detalle = "\n".join(f"- {error}" for error in errores[:10])
        raise RuntimeError(f"Se detuvo la consolidación. Archivos con error:\n{detalle}")

    historico = pd.concat(frames, ignore_index=True)
    print(f"Filas crudas: {len(historico):,}")
    frames.clear()
    gc.collect()

    base = transformar_dataset_base(historico)
    del historico
    gc.collect()
    if base is None or base.empty:
        raise RuntimeError("La transformación produjo un dataset vacío.")

    consolidado = calcular_reincidencias_vectorizadas(base)
    del base
    gc.collect()
    if consolidado.empty:
        raise RuntimeError("El cálculo de reincidencias produjo un dataset vacío.")

    semanas = pd.to_numeric(consolidado["Num_Semana_Archivo"], errors="coerce").dropna()
    rango = (f"Sem {int(semanas.min())} a Sem {int(semanas.max())}"
             if not semanas.empty else "sin semanas identificadas")

    salida.parent.mkdir(parents=True, exist_ok=True)
    temporal = salida.with_suffix(".tmp.parquet")
    consolidado.to_parquet(temporal, index=False, compression="snappy")
    temporal.replace(salida)
    print(f"\nParquet creado: {salida}")
    print(f"Registros finales: {len(consolidado):,} · {rango}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolida CSV históricos en un Parquet.")
    parser.add_argument("--source", default="datos_semanales", help="Carpeta que contiene los CSV.")
    parser.add_argument("--output", default="datos_semanales/datos_consolidados.parquet",
                        help="Ruta del Parquet consolidado.")
    args = parser.parse_args()

    raiz = Path(args.source).expanduser().resolve()
    salida = Path(args.output).expanduser().resolve()
    if not raiz.is_dir():
        raise NotADirectoryError(f"No existe la carpeta de datos: {raiz}")
    construir_parquet(raiz, salida)


if __name__ == "__main__":
    main()
