# ==============================================================================
# SISTEMA ENTERPRISE DE CONTROL OPERATIVO DE CUADRILLAS EN CAMPO 2026
# Archivo: app.py | Versión: 14.2.2-CONTRASTE-Y-CONFIRMACION
# ==============================================================================

import os
import re
import io
import logging
import base64
import csv
import hashlib
from urllib.parse import quote
import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(
    page_title="Control Operativo Cuadrillas 2026",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ControlCuadrillas.Monolith")    

import gc

# -----------------------------------------------------------------------------
# 0.2. MOTOR DE CONSULTA Y LECTURA OPTIMIZADA (REMOTO PARQUET + LOCAL + GITHUB API)
# -----------------------------------------------------------------------------
try:
    gh_cfg = st.secrets.get("github", {})
except FileNotFoundError:
    gh_cfg = {}
GITHUB_USER = gh_cfg.get("user", "jluisnavag-a11y")
GITHUB_REPO = gh_cfg.get("repo", "dashboard_jlng_operacoones_norte_cierre_diario")
GITHUB_BRANCH = gh_cfg.get("branch", "main")
GITHUB_FOLDER = gh_cfg.get("folder", "datos_semanales")
GITHUB_TOKEN = gh_cfg.get("token", "")

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{GITHUB_FOLDER}?ref={GITHUB_BRANCH}"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FOLDER}"


def descargar_y_procesar_archivo(nombre_archivo: str) -> Optional[pd.DataFrame]:
    """Descarga e ingesta individual de archivos remotos en fallback."""
    try:
        resp = requests.get(f"{GITHUB_RAW_BASE}/{nombre_archivo}", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            bytes_data = io.BytesIO(resp.content)
            if nombre_archivo.endswith(".parquet"):
                df_temp = pd.read_parquet(bytes_data)
            else:
                try:
                    df_temp = pd.read_csv(bytes_data, low_memory=False, dtype=str, encoding="utf-8", on_bad_lines="skip")
                except Exception:
                    bytes_data.seek(0)
                    df_temp = pd.read_csv(bytes_data, low_memory=False, dtype=str, encoding="latin1", on_bad_lines="skip")

            if df_temp is not None and not df_temp.empty:
                df_temp["Archivo_Origen"] = str(nombre_archivo)
                return df_temp
    except Exception as e:
        if 'logger' in globals():
            logger.error(f"Error procesando remotos {nombre_archivo}: {e}")
    return None


def transformar_dataset_completo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica TODAS las transformaciones de negocio (fechas de creación y término, folios,
    pólizas, dimensiones temporales, reincidencias) sobre un DataFrame crudo ya concatenado.

    Se reutiliza tanto en la ingestión completa (ejecutar_pipeline_ingestion_datos) como en
    la regeneración incremental del Parquet consolidado tras una carga manual, para que la
    lógica de negocio viva en un único lugar (antes estaba duplicada en varias funciones).
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    cols = list(df.columns)

    # Helpers de sanitización
    get_col = lambda alias: detectar_columna_por_patrones(cols, alias) if 'detectar_columna_por_patrones' in globals() else None
    sanit_fol = lambda s: s.apply(sanitizar_folio_identificador) if 'sanitizar_folio_identificador' in globals() else s
    sanit_txt = lambda s: s.apply(sanitizar_cadena_texto) if 'sanitizar_cadena_texto' in globals() else s

    # ---------------------------------------------------------------------
    # Parseo robusto de fechas (Excel serial + texto). Cubre "Fecha creacion FFM"
    # (obligatoria para las dimensiones temporales) y "Fecha termino" (nueva).
    # ---------------------------------------------------------------------
    col_fecha = get_col(LISTA_ALIAS_CREACION if 'LISTA_ALIAS_CREACION' in globals() else [])
    if col_fecha and col_fecha in df.columns and 'parsear_columna_fecha_robusta' in globals():
        df["_datetime_parsed"] = parsear_columna_fecha_robusta(df[col_fecha])
    else:
        df["_datetime_parsed"] = pd.NaT

    col_fecha_fin = get_col(LISTA_ALIAS_TERMINO if 'LISTA_ALIAS_TERMINO' in globals() else [])
    if col_fecha_fin and col_fecha_fin in df.columns and 'parsear_columna_fecha_robusta' in globals():
        df["_datetime_termino"] = parsear_columna_fecha_robusta(df[col_fecha_fin])
    else:
        df["_datetime_termino"] = pd.NaT

    # Tiempo de resolución (horas) entre creación y término. Se descartan deltas
    # negativos (datos mal capturados: término antes que creación).
    delta_horas = (df["_datetime_termino"] - df["_datetime_parsed"]).dt.total_seconds() / 3600.0
    df["Tiempo_Resolucion_Horas"] = delta_horas.where(delta_horas >= 0)

    # Columnas Principales
    c_os, c_cta, c_ot, c_tipo = get_col(LISTA_ALIAS_ORDEN if 'LISTA_ALIAS_ORDEN' in globals() else []), get_col(LISTA_ALIAS_CUENTA if 'LISTA_ALIAS_CUENTA' in globals() else []), get_col(LISTA_ALIAS_OT if 'LISTA_ALIAS_OT' in globals() else []), get_col(LISTA_ALIAS_TIPO if 'LISTA_ALIAS_TIPO' in globals() else [])
    c_usr, c_nom, c_prov, c_dist, c_cluster = get_col(LISTA_ALIAS_USUARIO if 'LISTA_ALIAS_USUARIO' in globals() else []), get_col(LISTA_ALIAS_NOMBRE if 'LISTA_ALIAS_NOMBRE' in globals() else []), get_col(LISTA_ALIAS_PROVEEDOR if 'LISTA_ALIAS_PROVEEDOR' in globals() else []), get_col(LISTA_ALIAS_DISTRITO if 'LISTA_ALIAS_DISTRITO' in globals() else []), get_col(LISTA_ALIAS_CLUSTER if 'LISTA_ALIAS_CLUSTER' in globals() else [])

    s_os = sanit_fol(df[c_os]) if c_os else "SIN_OS"
    s_cta = sanit_fol(df[c_cta]) if c_cta else "SIN_CTA"
    s_ot = sanit_fol(df[c_ot]) if c_ot else "SIN_OT"
    s_tipo = sanit_txt(df[c_tipo]) if c_tipo else "EVENTO GENERAL"

    df["FOLIO_KEY"] = s_os.astype(str) + "_" + s_cta.astype(str) + "_" + s_ot.astype(str) + "_" + s_tipo.astype(str)
    df["Cuenta_Cliente"] = s_cta.astype(str)

    s_u = sanit_txt(df[c_usr]) if c_usr else "SIN ESPECIFICAR"
    s_n = sanit_txt(df[c_nom]) if c_nom else "SIN ESPECIFICAR"
    df["Usuario_Tecnico"] = np.where((s_u != "SIN ESPECIFICAR") & (s_n != "SIN ESPECIFICAR"), s_u + " | " + s_n, np.where(s_n != "SIN ESPECIFICAR", s_n, s_u))

    df["Empresa"] = sanit_txt(df[c_prov]) if c_prov else "SIN PROVEEDOR"
    df["Distrito"] = sanit_txt(df[c_dist]) if c_dist else "DISTRITO GENERAL"
    df["Tipo_Orden"] = s_tipo
    df["Cluster_Raw"] = sanit_txt(df[c_cluster]) if c_cluster else "SIN CLUSTER"
    df["Cluster_Base"] = normalizar_clusters_vectorizado(df["Cluster_Raw"]) if 'normalizar_clusters_vectorizado' in globals() else df["Cluster_Raw"]

    # Mapeo de Pólizas
    c_pol = c_usr or c_nom
    if c_pol and 'MAPEO_POLIZAS' in globals():
        sub_cods = df[c_pol].astype(str).str[3:5]
        df["Codigo_Poliza"] = np.where(sub_cods.isin(MAPEO_POLIZAS.keys()), sub_cods, "")
        df["Nombre_Poliza"] = df["Codigo_Poliza"].map(MAPEO_POLIZAS).fillna("NO VALIDO")
    else:
        df["Codigo_Poliza"], df["Nombre_Poliza"] = "", "NO VALIDO"

    # Dimensiones Temporales
    semanas_archivo = df["Archivo_Origen"].apply(extraer_numero_semana_archivo) if "Archivo_Origen" in df.columns and 'extraer_numero_semana_archivo' in globals() else 0
    semanas_iso = pd.to_numeric(df["_datetime_parsed"].dt.isocalendar().week, errors="coerce").fillna(0).astype(int)
    df["Num_Semana_Archivo"] = np.where(semanas_archivo > 0, semanas_archivo, semanas_iso)

    anio_base = ANIO_BASE_ESTRICTO if 'ANIO_BASE_ESTRICTO' in globals() else 2026
    mapeo_meses = MAPEO_MESES_TEXTO if 'MAPEO_MESES_TEXTO' in globals() else {}

    df["AÑO_DIM"] = str(anio_base)
    df["SEMANA_DIM"] = df["Num_Semana_Archivo"].apply(lambda x: f"Semana {int(x)}" if x > 0 else "SIN_FECHA")
    df["MES_DIM"] = df["_datetime_parsed"].dt.month.fillna(1).astype(int).map(mapeo_meses).fillna("ENERO")

    dates_valid = df["_datetime_parsed"].dropna()
    df["FECHA_TRUNCADA"] = f"01.01.{anio_base}"
    if not dates_valid.empty:
        df.loc[dates_valid.index, "FECHA_TRUNCADA"] = dates_valid.dt.strftime(f"%d.%m.{anio_base}")

    if 'calcular_reincidencias_vectorizadas' in globals():
        df = calcular_reincidencias_vectorizadas(df)

    return df


@st.cache_data(ttl=86400, show_spinner="⚡ Cargando dataset...")
def ejecutar_pipeline_ingestion_datos(hash_archivos: str = "") -> pd.DataFrame:
    """
    Fuente única de datos: GitHub. Sin dependencia de disco local.

    1) Ruta rápida: descarga 'datos_consolidados.parquet' ya transformado (2-5 seg).
    2) Fallback: si no existe o está desactualizado, descarga todos los CSV/Parquet
       semanales en paralelo desde GitHub y aplica transformar_dataset_completo().
    """
    cols_base = [
        "FOLIO_KEY", "Cuenta_Cliente", "Usuario_Tecnico", "Empresa",
        "Distrito", "Tipo_Orden", "Cluster_Base", "Nombre_Poliza",
        "Num_Semana_Archivo", "SEMANA_DIM", "FECHA_TRUNCADA"
    ]

    # -------------------------------------------------------------------------
    # RUTA RÁPIDA: PARQUET CONSOLIDADO EN GITHUB (única fuente persistente)
    # -------------------------------------------------------------------------
    url_parquet_remoto = f"{GITHUB_RAW_BASE}/datos_consolidados.parquet"
    try:
        resp = requests.get(url_parquet_remoto, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            df = pd.read_parquet(io.BytesIO(resp.content))
            if not df.empty and "MES_DIM" in df.columns:
                return df
    except Exception as e:
        if 'logger' in globals():
            logger.warning(f"No se pudo descargar Parquet unificado remoto: {e}")

    # -------------------------------------------------------------------------
    # FALLBACK: DESCARGA COMPLETA DESDE GITHUB API (solo si no hay parquet válido)
    # -------------------------------------------------------------------------
    coleccion_dfs = []
    try:
        resp = requests.get(GITHUB_API_URL, headers=HEADERS, timeout=10)
        lista_archivos = [
            f["name"] for f in resp.json()
            if isinstance(f, dict) and f["name"].endswith((".csv", ".parquet")) and f["name"] != "datos_consolidados.parquet"
        ] if resp.status_code == 200 else []
    except Exception as e:
        if 'logger' in globals():
            logger.error(f"Error conectando con GitHub API: {e}")
        lista_archivos = []

    if lista_archivos:
        with ThreadPoolExecutor(max_workers=25) as executor:
            resultados = list(executor.map(descargar_y_procesar_archivo, lista_archivos))
        coleccion_dfs = [df for df in resultados if df is not None and not df.empty]

    if not coleccion_dfs:
        return pd.DataFrame(columns=cols_base)

    # -------------------------------------------------------------------------
    # TRANSFORMACIÓN Y UNIFICACIÓN DE DATOS (Crea MES_DIM, Pólizas, Fechas, etc.)
    # -------------------------------------------------------------------------
    df = pd.concat(coleccion_dfs, ignore_index=True)
    coleccion_dfs.clear()
    gc.collect()

    df = transformar_dataset_completo(df)
    return df

# -----------------------------------------------------------------------------
# 0.3. EJECUCIÓN CONTINUA DEL DASHBOARD
# -----------------------------------------------------------------------------
# Sustituye la llamada a la antigua función por esta variable general:



# ==============================================================================
# 1. CONSTANTES GLOBALES Y DICCIONARIOS DE NEGOCIO
# ==============================================================================

ANIO_BASE_ESTRICTO: int = 2026
EXCEL_EPOCH_START: pd.Timestamp = pd.Timestamp("1899-12-30")
NOMBRE_SISTEMA: str = "TOTALPLAY / OPERACIONES - REGIÓN NORTE LA BAJA"
VERSION_SISTEMA: str = "14.2.2-CONTRASTE-Y-CONFIRMACION"

MAPEO_POLIZAS: Dict[str, str] = {
    "R3": "RECOLECCIÓN",
    "E3": "PÓLIZA 3",
    "M3": "MULTIDISTRITO",
    "M4": "MULTIDISTRITO FLOTANTE",
    "MT": "MTTO PI",
    "D1": "DESTAJO"
}

DESCRIPCION_POLIZAS: Dict[str, str] = {
    "R3": "Póliza de Recolección de Equipos y Terminado de Operaciones en Campo",
    "E3": "Póliza Estándar Tipo 3 para Instalaciones y Mantenimiento Correctivo",
    "M3": "Póliza Multi-Distrito Operativa asignada a Zonas Urbanas de Alta Densidad",
    "M4": "Póliza Multi-Distrito Flotante para Cuadrillas de Respaldo Inter-Zona",
    "MT": "Póliza de Mantenimiento PI separada del cálculo estándar de volumen",
    "D1": "Póliza por Destajo asignada a Cuadrillas Contratistas Externas"
}

MAPEO_MESES_TEXTO: Dict[int, str] = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}

LISTA_ORDENADA_MESES: List[str] = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]

LISTA_ALIAS_CREACION: List[str] = ["creacion", "creación", "created", "fecha_creacion", "created_at", "f_creacion"]
LISTA_ALIAS_TERMINO: List[str] = ["termino", "término", "fecha termino", "fecha_termino", "f_termino", "fecha fin", "fecha_fin", "fecha cierre", "closed_at", "end_date", "finalizacion", "finalización"]
LISTA_ALIAS_GENERICOS: List[str] = ["fecha", "dia", "día", "date", "f_proceso", "fecha_ejecucion"]
LISTA_ALIAS_USUARIO: List[str] = ["usuario instalador", "usuario_instalador", "instalador", "usuario", "usr", "id_usuario", "tech_id"]
LISTA_ALIAS_NOMBRE: List[str] = ["nombre tecnico", "nombre técnico", "nombre_tecnico", "tecnico", "técnico", "nombre", "tech_name"]
LISTA_ALIAS_ORDEN: List[str] = ["orden_servicio", "orden servicio", "os", "orden", "folio", "service_order"]
LISTA_ALIAS_CUENTA: List[str] = ["cuenta", "account", "cta", "num_cuenta"]
LISTA_ALIAS_OT: List[str] = ["ot", "orden_trabajo", "orden trabajo", "work_order", "num_ot"]
LISTA_ALIAS_TIPO: List[str] = ["tipo de orden", "tipo_orden", "tipo orden", "tipo_evento", "tipo", "evento", "order_type"]
LISTA_ALIAS_PROVEEDOR: List[str] = ["empresa", "proveedor", "contratista", "vendor", "company"]
LISTA_ALIAS_DISTRITO: List[str] = ["distrito", "region", "región", "zona", "sucursal", "district"]
LISTA_ALIAS_CLUSTER: List[str] = ["cluster", "clúster", "nodo", "sector", "zona_cluster", "ampliacion"]

# PALETA OFICIAL TOTALPLAY LA BAJA
PALETA_COLOR: Dict[str, str] = {
    "azul_noche": "#0B192C",
    "azul_marina": "#1E3E62",
    "turquesa_cyan": "#00D2C8",
    "verde_montana": "#10B981",
    "naranja_desierto": "#F97316",
    "amarillo_sol": "#FBBF24",
    "blanco_puro": "#FFFFFF",
    "gris_borde": "#CBD5E1",
    "texto_negro": "#000000"
}

MAPEO_BASE_CLUSTERS: Dict[str, str] = {
    "AGUA CALIENTE": "AGUA CALIENTE",
    "ALTAMIRA": "ALTAMIRA",
    "AVIACION": "AVIACION",
    "CERRO COLORADO": "CERRO COLORADO",
    "COLINAS DEL FLORIDO": "COLINAS DEL FLORIDO",
    "DOS MIL": "DOS MIL",
    "FONTANA": "FONTANA",
    "FUNDADORES": "FUNDADORES",
    "GONZALEZ ORTEGA": "GONZALEZ ORTEGA",
    "HACIENDA REAL": "HACIENDA REAL",
    "LOMAS DE VIRREY": "LOMAS DE VIRREY",
    "MAGISTERIAL": "MAGISTERIAL",
    "MATAMOROS": "MATAMOROS",
    "NATURA": "NATURA",
    "OTAY": "OTAY",
    "PACIFICO": "PACIFICO",
    "PATRIA NUEVA": "PATRIA NUEVA",
    "PLAYA HERMOSA": "PLAYA HERMOSA",
    "PLAYAS TIJUANA": "PLAYAS TIJUANA",
    "PUEBLO NUEVO": "PUEBLO NUEVO",
    "REFUGIO": "REFUGIO",
    "ROSAMAR": "ROSAMAR",
    "ROSAS MAGALLON": "ROSAS MAGALLON",
    "SALVATIERRA": "SALVATIERRA",
    "SANTA FE TIJUANA": "SANTA FE TIJUANA",
    "UABC": "UABC",
    "TECATE": "TECATE",
    "VALLE VERDE": "VALLE VERDE",
    "VILLA DEL CAMPO": "VILLA DEL CAMPO",
    "ZAPATA": "ZAPATA"
}

RE_SEMANA = re.compile(r"(?:SEM|SEMANA|S)[\s_\-]*(\d{1,2})", re.IGNORECASE)
# ==============================================================================
# 2. DATACLASSES
# ==============================================================================

@dataclass
class MetricasResumenKPI:
    total_eventos: int
    total_usuarios: int
    dias_operativos: int
    productividad_diaria: float
    eventos_r3: int
    eventos_mt: int


# ==============================================================================
# 4. FUNCIONES AUXILIARES DE TRANSFORMACIÓN Y LIMPIEZA
# ==============================================================================

def sanitizar_cadena_texto(val: Any) -> str:
    if pd.isna(val) or val is None:
        return "SIN ESPECIFICAR"
    txt = str(val).strip()
    if txt == "" or txt.lower() in ["nan", "null", "none", "<na>"]:
        return "SIN ESPECIFICAR"
    return re.sub(r"\s+", " ", txt).upper()

def sanitizar_folio_identificador(val: Any) -> str:
    if pd.isna(val) or val is None:
        return "SIN_FOLIO"
    txt = str(val).strip().upper()
    txt = re.sub(r"[^A-Z0-9\-_]", "", txt)
    return txt if txt else "SIN_FOLIO"

def extraer_numero_semana_archivo(nombre_archivo):
    if not isinstance(nombre_archivo, str) or pd.isna(nombre_archivo):
        return None
    match = RE_SEMANA.search(nombre_archivo)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, TypeError):
            pass
    # Si la cadena ya era un número puro (ej: "24")
    if str(nombre_archivo).strip().isdigit():
        return int(nombre_archivo)
    return None

def _normalizar_cluster_string(txt: str) -> str:
    if txt == "SIN ESPECIFICAR":
        return "CLUSTER GENERAL"

    txt_clean = re.sub(r"^AMPLIACI[OÓ]N\s+", "", txt)
    txt_clean = re.sub(r"_\d+_[A-Z]$", "", txt_clean)
    txt_clean = re.sub(r"_[A-Z]$", "", txt_clean)
    txt_clean = re.sub(r"\s+\d+$", "", txt_clean).strip()

    for c_base in MAPEO_BASE_CLUSTERS.keys():
        if c_base in txt_clean or txt_clean in c_base:
            return c_base

    return txt_clean if txt_clean else "CLUSTER GENERAL"

def normalizar_clusters_vectorizado(serie_cluster: pd.Series) -> pd.Series:
    unicos = serie_cluster.unique()
    mapa_normalizado = {val: _normalizar_cluster_string(str(val)) for val in unicos}
    return serie_cluster.map(mapa_normalizado)

def detectar_columna_por_patrones(columnas: List[str], patrones: List[str]) -> Optional[str]:
    for col in columnas:
        col_lower = str(col).lower().strip()
        for patron in patrones:
            if patron.lower() in col_lower:
                return col
    return None

def parsear_columna_fecha_robusta(serie_raw: pd.Series) -> pd.Series:
    if serie_raw.empty:
        return pd.Series(pd.NaT, index=serie_raw.index)
    
    serie_num = pd.to_numeric(serie_raw, errors="coerce")
    mask_num = serie_num.notna()
    serie_res = pd.Series(pd.NaT, index=serie_raw.index)

    if mask_num.any():
        sub_num = serie_num[mask_num]
        mask_excel = (sub_num >= 20000) & (sub_num <= 80000)
        if mask_excel.any():
            idx_excel = sub_num[mask_excel].index
            serie_res.loc[idx_excel] = pd.to_datetime(
                sub_num[mask_excel], unit="D", origin=EXCEL_EPOCH_START, errors="coerce"
            )

    mask_txt = ~mask_num
    if mask_txt.any():
        sub_txt = serie_raw[mask_txt].astype(str).str.strip()
        serie_res.loc[mask_txt] = pd.to_datetime(sub_txt, dayfirst=True, errors="coerce")

    return serie_res

# ==============================================================================
# 5. METRICAS Y GRAFICOS
# ==============================================================================

def calcular_indice_productividad_diaria(total_eventos: int, total_usuarios: int, dias_operativos: int) -> float:
    if total_usuarios <= 0 or dias_operativos <= 0:
        return 0.0
    prod_raw = (total_eventos / total_usuarios) / dias_operativos
    prod_norm = prod_raw * 1.85 if prod_raw < 1.0 else prod_raw
    return round(min(prod_norm, 3.85), 2)

def calcular_tendencia_lineal_robusta(valores: List[float]) -> List[float]:
    n = len(valores)
    if n <= 1:
        return [float(v) for v in valores]
    
    x = np.arange(n)
    y = np.array(valores, dtype=float)
    z = np.polyfit(x, y, 1)
    p = np.poly1d(z)
    return [round(float(v), 2) for v in p(x)]

def extraer_metricas_kpi_totales(df_folios: pd.DataFrame) -> MetricasResumenKPI:
    total_eventos = len(df_folios)
    total_usuarios = df_folios["Usuario_Tecnico"].nunique() if total_eventos > 0 else 0
    dias_operativos = df_folios["FECHA_TRUNCADA"].nunique() if total_eventos > 0 else 0

    prod_diaria = calcular_indice_productividad_diaria(total_eventos, total_usuarios, dias_operativos)
    conteo_r3 = (df_folios["Codigo_Poliza"] == "R3").sum()
    conteo_mt = (df_folios["Codigo_Poliza"] == "MT").sum()

    return MetricasResumenKPI(
        total_eventos=total_eventos,
        total_usuarios=total_usuarios,
        dias_operativos=dias_operativos,
        productividad_diaria=prod_diaria,
        eventos_r3=conteo_r3,
        eventos_mt=conteo_mt
    )

def generar_figura_evolucion_temporal(df_folios: pd.DataFrame, dimension_temporal: str) -> go.Figure:
    if df_folios.empty:
        fig_empty = go.Figure()
        fig_empty.update_layout(title="Sin datos para la selección actual")
        return fig_empty

    # 1. Copia temporal y conversión limpia de semanas
    df_temp = df_folios.copy()
    def _num_sem(val):
        try:
            return int(float(str(val).replace("Semana", "").strip()))
        except (ValueError, TypeError):
            return 999

    # 2. Normalizar la columna SEMANA_DIM en la copia antes de agrupar
    if dimension_temporal == "SEMANA_DIM":
        df_temp["SEMANA_DIM"] = df_temp["SEMANA_DIM"].apply(
            lambda s: f"Semana {_num_sem(s)}" if _num_sem(s) != 999 else str(s)
        )
        raw_semanas = [x for x in df_temp["SEMANA_DIM"].dropna().unique() if pd.notna(x)]
        eje_x_base = sorted(raw_semanas, key=_num_sem)
    elif dimension_temporal == "MES_DIM":
        eje_x_base = [m for m in LISTA_ORDENADA_MESES if m in df_temp["MES_DIM"].unique()]
    elif dimension_temporal == "FECHA_TRUNCADA":
        eje_x_base = sorted(list(df_temp["FECHA_TRUNCADA"].unique()))
    else:
        eje_x_base = [str(ANIO_BASE_ESTRICTO)]

    # 3. Agrupaciones sobre la copia homogeneizada (¡Aquí se resuelve la coincidencia de llaves!)
    mapa_eventos = df_temp.groupby(dimension_temporal).size().to_dict()
    mapa_usuarios = df_temp.groupby(dimension_temporal)["Usuario_Tecnico"].nunique().to_dict()
    mapa_dias = df_temp.groupby(dimension_temporal)["FECHA_TRUNCADA"].nunique().to_dict()

    eje_x, valores_eventos, valores_prod = [], [], []

    for cat in eje_x_base:
        ev = mapa_eventos.get(cat, 0)
        if ev > 0:
            eje_x.append(str(cat))
            valores_eventos.append(float(ev))
            u = max(mapa_usuarios.get(cat, 1), 1)
            d = max(mapa_dias.get(cat, 1), 1)
            valores_prod.append(calcular_indice_productividad_diaria(ev, u, d))
    if not eje_x:
        fig_empty = go.Figure()
        fig_empty.update_layout(title="Sin registros activos en el rango seleccionado")
        return fig_empty

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=eje_x, y=valores_eventos, name="Eventos Completados",
        marker_color=PALETA_COLOR["azul_marina"], opacity=0.9, yaxis="y"
    ))

    tend_eventos = calcular_tendencia_lineal_robusta(valores_eventos)
    fig.add_trace(go.Scatter(
        x=eje_x, y=tend_eventos, name="Tendencia Eventos", mode="lines",
        line=dict(color=PALETA_COLOR["naranja_desierto"], width=3, dash="dash"), yaxis="y"
    ))

    fig.add_trace(go.Scatter(
        x=eje_x, y=valores_prod, name="Productividad Diaria", mode="lines+markers",
        line=dict(color=PALETA_COLOR["turquesa_cyan"], width=3),
        marker=dict(size=8, color=PALETA_COLOR["azul_noche"]), yaxis="y2"
    ))

    tend_prod = calcular_tendencia_lineal_robusta(valores_prod)
    fig.add_trace(go.Scatter(
        x=eje_x, y=tend_prod, name="Tendencia Productividad", mode="lines",
        line=dict(color=PALETA_COLOR["verde_montana"], width=2, dash="dot"), yaxis="y2"
    ))

    fig.update_layout(
        title={
            "text": f"<b>EVOLUCIÓN TEMPORAL Y TENDENCIAS AJUSTADAS ({dimension_temporal})</b>",
            "y": 0.96, "x": 0.01,
            "font": {"size": 14, "color": "#000000", "family": "Plus Jakarta Sans"}
        },
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        font={"family": "Plus Jakarta Sans, sans-serif", "size": 12, "color": "#000000"},
        margin=dict(l=50, r=60, t=50, b=80),
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="top", y=-0.22, xanchor="center", x=0.5,
            font=dict(size=11, color="#000000")
        ),
        xaxis=dict( 
            showgrid=False, linecolor=PALETA_COLOR["azul_marina"],
            tickfont=dict(color="#000000", size=11, weight="bold"),
            type='category',
            categoryorder='array',
            categoryarray=eje_x_base
        ),
        yaxis=dict(
            title=dict(text="OT / Eventos Completados", font=dict(color="#000000", size=12, weight="bold")),
            showgrid=True, gridcolor="#E2E8F0", linecolor=PALETA_COLOR["azul_marina"],
            tickfont=dict(color="#000000", size=11, weight="bold")
        ),
        yaxis2=dict(
            title=dict(text="Productividad (Eventos / Técnico / Día)", font=dict(color="#000000", size=12, weight="bold")),
            overlaying="y", side="right", showgrid=False, linecolor=PALETA_COLOR["azul_marina"],
            tickfont=dict(color="#000000", size=11, weight="bold"),
            range=[0, max(valores_prod + [4.0]) * 1.25]
        )
    )

    return fig

# ==============================================================================
# 6. PIPELINE DE INGESTIÓN MULTI-ENCODING + ACTUALIZACIÓN FORZADA
# ==============================================================================

def es_evento_soporte(tipo_str: str) -> bool:
    if pd.isna(tipo_str):
        return False
    txt = str(tipo_str).upper()
    return "SOPORTE" in txt or "SOP" in txt

def obtener_valor_tipo2(valor_causa, valor_tipo_orden) -> str:
    """Si la causa es None, N/A, nula o vacía, la reemplaza por el valor de TIPO / Tipo_Orden."""
    val_tipo = str(valor_tipo_orden).strip() if pd.notna(valor_tipo_orden) else "SIN TIPO"
    if val_tipo.upper() in ["NONE", "NULL", "NA", "N/A", "NAN", ""]:
        val_tipo = "SIN TIPO"

    if pd.isna(valor_causa):
        return val_tipo

    val_causa_str = str(valor_causa).strip()
    if val_causa_str.upper() in ["NONE", "NULL", "NA", "N/A", "NAN", ""]:
        return val_tipo

    return val_causa_str

def calcular_reincidencias_vectorizadas(df: pd.DataFrame) -> pd.DataFrame:
    # Inicialización de columnas por defecto
    df["ES_REINCIDENCIA"] = "NO"
    df["CONTEO_PREVIO_8_SEM"] = 0
    df["Usuario_Origen_Reincidencia"] = "N/A"
    df["Empresa_Origen_Reincidencia"] = "N/A"
    df["Semana_Origen_Reincidencia"] = np.nan
    df["Causa_Origen"] = "N/A"
    df["TIPO_2"] = "N/A"
    df["Falla_Nueva"] = "N/A"
    df["ES_CASO_ESPECIAL"] = "NO"

    if df.empty:
        return df

    cols = list(df.columns)

    # Detectar dinámicamente las columnas necesarias
    col_tech = detectar_columna_por_patrones(cols, ["usuario_tecnico", "tecnico", "tech", "usuario", "atendio", "nombre_tecnico"]) or "Usuario_Tecnico"
    col_semana = detectar_columna_por_patrones(cols, ["num_semana_archivo", "semana", "sem"]) or "Num_Semana_Archivo"
    col_causa = detectar_columna_por_patrones(cols, ["causa", "motivo", "subtipo", "diagnostico"]) or "Tipo_Orden"
    col_falla = detectar_columna_por_patrones(cols, ["falla", "observaciones", "descripcion"]) or "Tipo_Orden"
    col_empresa = detectar_columna_por_patrones(cols, ["empresa", "proveedor", "vendor"]) or "Empresa"
    col_tipo = "Tipo_Orden" if "Tipo_Orden" in cols else ("TIPO" if "TIPO" in cols else col_causa)

    # Preservar el orden original
    df["_INDEX_ORIGINAL"] = range(len(df))
    df["_SEM_TEMP"] = pd.to_numeric(df[col_semana], errors="coerce").fillna(0).astype(int)

    # Filtrar cuentas válidas
    mask_cta_valida = df["Cuenta_Cliente"].notna() & (~df["Cuenta_Cliente"].astype(str).str.upper().isin(["SIN_CTA", "SIN_FOLIO", "NAN", "NONE", ""]))
    
    # Ordenar por Cuenta y Cronología
    df_valid = df[mask_cta_valida].sort_values(by=["Cuenta_Cliente", "_SEM_TEMP", "_INDEX_ORIGINAL"]).copy()

    dict_reincidencias = {}

    # Lógica de reincidencia (Evento N-1)
    for cuenta, g in df_valid.groupby("Cuenta_Cliente"):
        registros = g.to_dict("records")
        n = len(registros)
        if n < 2:
            continue

        for i in range(1, n):
            reg_actual = registros[i]
            tipo_actual = reg_actual.get("Tipo_Orden", reg_actual.get("TIPO", ""))

            # Si la visita actual es un SOPORTE -> Reincidencia
            if es_evento_soporte(tipo_actual):
                reg_prev = registros[i - 1]  # Evento inmediatamente anterior (visita n-1)
                
                tech_prev = str(reg_prev.get(col_tech, "SIN ESPECIFICAR"))
                if tech_prev.upper() in ["NAN", "NONE", "", "N/A", "NULL"]:
                    tech_prev = "SIN ESPECIFICAR"

                emp_prev = str(reg_prev.get(col_empresa, "SIN EMPRESA"))
                if emp_prev.upper() in ["NAN", "NONE", "", "N/A", "NULL"]:
                    emp_prev = "SIN EMPRESA"

                # Generar TIPO_2 resolviendo el fallback si la causa es NA/None
                causa_raw = reg_prev.get(col_causa)
                tipo_raw = reg_prev.get(col_tipo)
                tipo_2_val = obtener_valor_tipo2(causa_raw, tipo_raw)
                falla_val = reg_actual.get(col_falla, "N/A")

                key_actual = reg_actual.get("FOLIO_KEY")
                dict_reincidencias[key_actual] = {
                    "Usuario_Origen": tech_prev,
                    "Empresa_Origen": emp_prev,
                    "Semana_Origen": reg_prev.get("_SEM_TEMP", np.nan),
                    "Causa_Origen": str(causa_raw) if pd.notna(causa_raw) else "N/A",
                    "TIPO_2": tipo_2_val,
                    "Falla_Nueva": falla_val
                }

    # Asignar resultados al DataFrame principal
    if dict_reincidencias:
        keys_rein = set(dict_reincidencias.keys())
        mask_rein = df["FOLIO_KEY"].isin(keys_rein)

        df.loc[mask_rein, "ES_REINCIDENCIA"] = "SI"
        df.loc[mask_rein, "CONTEO_PREVIO_8_SEM"] = 1

        df.loc[mask_rein, "Usuario_Origen_Reincidencia"] = df.loc[mask_rein, "FOLIO_KEY"].map(
            lambda k: dict_reincidencias[k]["Usuario_Origen"] if k in dict_reincidencias else "N/A"
        )
        df.loc[mask_rein, "Empresa_Origen_Reincidencia"] = df.loc[mask_rein, "FOLIO_KEY"].map(
            lambda k: dict_reincidencias[k]["Empresa_Origen"] if k in dict_reincidencias else "N/A"
        )
        df.loc[mask_rein, "Semana_Origen_Reincidencia"] = df.loc[mask_rein, "FOLIO_KEY"].map(
            lambda k: dict_reincidencias[k]["Semana_Origen"] if k in dict_reincidencias else np.nan
        )
        df.loc[mask_rein, "Causa_Origen"] = df.loc[mask_rein, "FOLIO_KEY"].map(
            lambda k: dict_reincidencias[k]["Causa_Origen"] if k in dict_reincidencias else "N/A"
        )
        df.loc[mask_rein, "TIPO_2"] = df.loc[mask_rein, "FOLIO_KEY"].map(
            lambda k: dict_reincidencias[k]["TIPO_2"] if k in dict_reincidencias else "N/A"
        )
        df.loc[mask_rein, "Falla_Nueva"] = df.loc[mask_rein, "FOLIO_KEY"].map(
            lambda k: dict_reincidencias[k]["Falla_Nueva"] if k in dict_reincidencias else "N/A"
        )

    # Limpiar auxiliares
    df.drop(columns=["_INDEX_ORIGINAL", "_SEM_TEMP"], errors="ignore", inplace=True)

    return df

# ==============================================================================
# CSS DE ALTO IMPACTO (COMPATIBLE CON STREAMLIT CLOUD Y LOCALHOST)
# ==============================================================================

def inyectar_estilos_base_ui() -> None:
    st.markdown("""
        <style>
        /* 1. CONTENEDOR PRINCIPAL DE LAS PESTAÑAS (TABS) */
        div[data-testid="stTabs"] {
            background-color: #0f172a !important;
            padding: 8px !important;
            border-radius: 12px !important;
            border: 1px solid #1e293b !important;
        }

        /* BARRA DE LISTA DE TABS */
        div[data-testid="stTabs"] > div[role="tablist"] {
            gap: 8px !important;
            background-color: transparent !important;
            border-bottom: none !important;
        }

        /* 2. ESTILO BASE DE CADA BOTÓN/TAB */
        div[data-testid="stTabs"] button[role="tab"] {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
            padding: 10px 20px !important;
            transition: all 0.25s ease-in-out !important;
        }

        /* TEXTO DENTRO DE LA PESTAÑA */
        div[data-testid="stTabs"] button[role="tab"] p,
        div[data-testid="stTabs"] button[role="tab"] span {
            color: #94a3b8 !important;
            font-size: 14px !important;
            font-weight: 600 !important;
        }

        /* 3. HOVER EN TABS */
        div[data-testid="stTabs"] button[role="tab"]:hover {
            background-color: #334155 !important;
            border-color: #475569 !important;
        }
        div[data-testid="stTabs"] button[role="tab"]:hover p {
            color: #f8fafc !important;
        }

        /* 4. PESTAÑA ACTIVA */
        div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
            background-color: #0284c7 !important;
            border-color: #38bdf8 !important;
            box-shadow: 0 4px 12px rgba(2, 132, 199, 0.4) !important;
        }

        div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p {
            color: #ffffff !important;
            font-weight: 700 !important;
        }

        /* ELIMINAR LÍNEA INFERIOR NATIVA DE STREAMLIT */
        div[data-testid="stTabs"] div[data-baseweb="tab-highlight"] {
            display: none !important;
        }

        /* 5. FIX DE CONTRASTE PARA LABELS DE FILTROS Y CONTROLES (MULTISELECT, SELECTBOX) */
        div[data-widget="stMultiSelect"] label,
        div[data-widget="stSelectbox"] label,
        div[data-widget="stTextInput"] label,
        div[data-widget="stTextArea"] label,
        div[data-baseweb="select"] label {
            color: #f1f5f9 !important;
            font-weight: 600 !important;
            font-size: 13px !important;
            letter-spacing: 0.3px !important;
            margin-bottom: 4px !important;
        }

        /* 6. FIX DE VISIBILIDAD DE TÍTULOS Y TEXTO EN MÓDULOS DE REINCIDENCIAS */
        div[data-testid="stTabs"] .stMarkdown p,
        div[data-testid="stTabs"] .stMarkdown h1,
        div[data-testid="stTabs"] .stMarkdown h2,
        div[data-testid="stTabs"] .stMarkdown h3,
        div[data-testid="stTabs"] .stMarkdown h4 {
            color: #f8fafc !important;
        }

        /* 7. FIX PARA TEXTAREA Y INPUTS EN MODO OSCURO */
        div[data-testid="stTextArea"] textarea, 
        div[data-testid="stTextInput"] input {
            background-color: #0f172a !important;
            color: #f8fafc !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
            font-family: monospace !important;
        }
        
        div[data-testid="stTextArea"] textarea:focus, 
        div[data-testid="stTextInput"] input:focus {
            border-color: #38bdf8 !important;
            box-shadow: 0 0 0 1px #38bdf8 !important;
        }

        /* 8. CONTENEDOR INTERNO DE DESPLEGABLES (INPUT BOX) */
        div[data-baseweb="select"] > div {
            background-color: #0f172a !important;
            border-color: #334155 !important;
            color: #f8fafc !important;
            border-radius: 8px !important;
        }
        </style>
    """, unsafe_allow_html=True)

# ==============================================================================
# 7. VISTAS Y SECCIONES (OPTIMIZACIÓN VECTORIZADA DE ALTO RENDIMIENTO)
# ==============================================================================

def renderizar_pestana_polizas_cuadrillas(df_folios: pd.DataFrame, dimension_sel: str) -> None:
    inyectar_estilos_base_ui()
    kpis = extraer_metricas_kpi_totales(df_folios)

    sub_tab1, sub_tab2, sub_tab3, sub_tab4, sub_tab5 = st.tabs([
        "📈 Evolución & Productividad",
        "📊 Desglose por Pólizas",
        "🏆 Ranking de Cuadrillas / Técnicos",
        "📥 Descarga de Reportes",
        "📂 Cargar Datos Localmente"
    ])

    # Mostrar la captura antes de procesar los gráficos de las otras pestañas.
    with sub_tab5:
        st.button("📋 Abrir caja de pegado a todo el ancho", on_click=abrir_captura_completa, key="abrir_captura_ancha")

    # --------------------------------------------------------------------------
    # SUBTAB 1: EVOLUCIÓN & PRODUCTIVIDAD
    # --------------------------------------------------------------------------
    with sub_tab1:
        mask_grafico = df_folios[dimension_sel].notnull() & (~df_folios[dimension_sel].astype(str).str.lower().isin(["nan", "none", "null", ""]))
        df_folios_grafico = df_folios[mask_grafico]
        
        fig_evolucion = generar_figura_evolucion_temporal(df_folios_grafico, dimension_sel)
        st.plotly_chart(fig_evolucion, width="stretch", key="grafico_evolucion_temporal_polizas", config={'displayModeBar': False})

        nom_dim_label = {
            "FECHA_TRUNCADA": "Día",
            "SEMANA_DIM": "Semana",
            "MES_DIM": "Mes",
            "AÑO_DIM": "Año"
        }.get(dimension_sel, "Período")

        st.markdown(f"""
        <div class="matrix-title-card" style="background:#1e293b; padding:12px; border-radius:8px; margin-bottom:12px; border:1px solid #334155;">
            <b style="color:#f8fafc; font-size:15px;">👥 CUADRILLAS/TÉCNICOS FIRMADOS POR {nom_dim_label.upper()} Y PROVEEDOR</b>
            <p style="color:#94a3b8; margin:2px 0 0 0; font-size:12px;">Conteo de usuarios técnicos únicos con actividad registrada por período.</p>
        </div>
        """, unsafe_allow_html=True)

        if not df_folios.empty:
            df_matriz = pd.pivot_table(
                df_folios,
                index="Empresa",
                columns=dimension_sel,
                values="Usuario_Tecnico",
                aggfunc="nunique",
                fill_value=0
            )
            cols_raw = list(df_matriz.columns)

            if dimension_sel == "FECHA_TRUNCADA":
                cols_ordenadas = sorted(
                    cols_raw,
                    key=lambda x: pd.to_datetime(x, format="%d.%m.%Y", errors="coerce")
                    if pd.notna(pd.to_datetime(x, format="%d.%m.%Y", errors="coerce")) else str(x)
                )
            elif dimension_sel == "MES_DIM":
                cols_ordenadas = [m for m in LISTA_ORDENADA_MESES if m in cols_raw]
            else:
                def extraer_numero(texto):
                    nums = re.findall(r'\d+', str(texto))
                    return int(nums[0]) if nums else 99999
                cols_validas = [c for c in cols_raw if str(c).lower() not in ["nan", "none", "null", ""]]
                cols_ordenadas = sorted(cols_validas, key=extraer_numero)

            df_matriz = df_matriz[cols_ordenadas]
            df_matriz.columns = [
                f"Semana {int(float(str(c).replace('Semana','').strip()))}" 
                if "Semana" in str(c) and ".0" in str(c) else str(c) 
                for c in df_matriz.columns
            ]
            cols_ordenadas_limpias = list(df_matriz.columns)

            # Vectorización optimizada de arrays con NumPy
            matriz_vals = df_matriz[cols_ordenadas_limpias].to_numpy()
            df_matriz["TENDENCIA"] = matriz_vals.tolist()
            df_matriz["PROMEDIO_PERIODO"] = np.round(matriz_vals.mean(axis=1), 1)

            df_totales = df_folios.groupby(dimension_sel)["Usuario_Tecnico"].nunique()
            fila_total_serie = df_totales.reindex(cols_ordenadas).fillna(0).astype(int)

            dict_total = dict(zip(cols_ordenadas_limpias, fila_total_serie.values))
            dict_total["TENDENCIA"] = fila_total_serie.tolist()
            dict_total["PROMEDIO_PERIODO"] = round(float(fila_total_serie.mean()), 1)
            
            df_total = pd.DataFrame([dict_total], index=["TOTAL GENERAL"])
            df_matriz = pd.concat([df_matriz, df_total])

            columnas_finales = ["TENDENCIA"] + cols_ordenadas_limpias + ["PROMEDIO_PERIODO"]
            df_matriz_final = df_matriz[columnas_finales].reset_index().rename(columns={"index": "Empresa"})
            
            st.dataframe(
                df_matriz_final,
                column_config={
                    "Empresa": st.column_config.Column("Empresa", width="medium", pinned=True),
                    "TENDENCIA": st.column_config.LineChartColumn("Tendencia", width="small", y_min=0, pinned=True),
                    "PROMEDIO_PERIODO": st.column_config.NumberColumn("PROMEDIO_PERIODO", format="%.1f")
                },
                width="stretch", hide_index=True, height=320
            )
            
            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                df_pol = df_folios.groupby(["Nombre_Poliza"], observed=True).size().reset_index(name="Total_Eventos").sort_values(by="Total_Eventos", ascending=True)
                fig_pol = px.bar(
                    df_pol, x="Total_Eventos", y="Nombre_Poliza", orientation="h", text="Total_Eventos",
                    title="<b>VOLUMEN TOTAL POR TIPO DE PÓLIZA</b>",
                    color_discrete_sequence=[PALETA_COLOR["azul_marina"]]
                )
                fig_pol.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(family="Plus Jakarta Sans", size=12, color="#E2E8F0"),
                    title=dict(font=dict(color="#FFFFFF", size=14)),
                    xaxis=dict(showgrid=True, gridcolor="#334155", tickfont=dict(color="#E2E8F0", size=11, weight="bold")),
                    yaxis=dict(tickfont=dict(color="#E2E8F0", size=11, weight="bold"))
                )
                fig_pol.update_traces(textposition="outside", textfont=dict(color="#FFFFFF", size=11, weight="bold"))
                st.plotly_chart(fig_pol, width="stretch", config={'displayModeBar': False})

            with col2:
                df_eve = df_folios.groupby("Tipo_Orden", observed=True).size().reset_index(name="Total_Eventos").sort_values(by="Total_Eventos", ascending=True).tail(10)
                fig_eve = px.bar(
                    df_eve, x="Total_Eventos", y="Tipo_Orden", orientation="h", text="Total_Eventos",
                    title="<b>TOP 10 TIPOS DE EVENTO / ORDEN</b>",
                    color_discrete_sequence=[PALETA_COLOR["turquesa_cyan"]]
                )
                fig_eve.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(family="Plus Jakarta Sans", size=12, color="#E2E8F0"),
                    title=dict(font=dict(color="#FFFFFF", size=14)),
                    xaxis=dict(showgrid=True, gridcolor="#334155", tickfont=dict(color="#E2E8F0", size=11, weight="bold")),
                    yaxis=dict(tickfont=dict(color="#E2E8F0", size=11, weight="bold"))
                )
                fig_eve.update_traces(textposition="outside", textfont=dict(color="#FFFFFF", size=11, weight="bold"))
                st.plotly_chart(fig_eve, width="stretch", config={'displayModeBar': False})

    # --------------------------------------------------------------------------
    # SUBTAB 2: DESGROSE POR PÓLIZAS
    # --------------------------------------------------------------------------
    with sub_tab2:
        st.markdown("### 📂 Resumen Operativo por Tipo de Póliza Catalogada")
        if not df_folios.empty:
            df_res_pol = (
                df_folios.groupby(["Codigo_Poliza", "Nombre_Poliza"], observed=True)
                .agg(
                    Total_Eventos=("FOLIO_KEY", "count"),
                    Tecnicos_Unicos=("Usuario_Tecnico", "nunique"),
                    Dias_Operativos=("FECHA_TRUNCADA", "nunique")
                ).reset_index()
            )
            v_calc_prod = np.vectorize(calcular_indice_productividad_diaria)
            df_res_pol["Productividad_Promedio"] = v_calc_prod(
                df_res_pol["Total_Eventos"].to_numpy(),
                df_res_pol["Tecnicos_Unicos"].to_numpy(),
                df_res_pol["Dias_Operativos"].to_numpy()
            )
            st.dataframe(df_res_pol, width="stretch", hide_index=True)

    # --------------------------------------------------------------------------
    # SUBTAB 3: RANKING DE CUADRILLAS / TÉCNICOS
    # --------------------------------------------------------------------------
    with sub_tab3:
        st.markdown("### 🏆 Ranking de Productividad por Cuadrilla / Técnico")
        if not df_folios.empty:
            df_rank = (
                df_folios.groupby(["Usuario_Tecnico", "Codigo_Poliza", "Nombre_Poliza", "Empresa"], observed=True)
                .agg(Eventos_Totales=("FOLIO_KEY", "count"), Dias_Activos=("FECHA_TRUNCADA", "nunique"))
                .reset_index()
            )
            v_calc_prod = np.vectorize(calcular_indice_productividad_diaria)
            df_rank["Productividad_Diaria"] = v_calc_prod(
                df_rank["Eventos_Totales"].to_numpy(),
                1,
                df_rank["Dias_Activos"].to_numpy()
            )
            df_rank = df_rank.sort_values(by="Productividad_Diaria", ascending=False)
            st.dataframe(df_rank, width="stretch", hide_index=True, height=400)

    # --------------------------------------------------------------------------
    # SUBTAB 4: DESCARGA DE REPORTES
    # --------------------------------------------------------------------------
    with sub_tab4:
        st.markdown("### 📥 Descarga de Reportes")
        csv_bytes = df_folios.to_csv(index=False).encode("utf-8")
        st.download_button("📄 Descargar Dataset (CSV)", csv_bytes, f"Reporte_{ANIO_BASE_ESTRICTO}.csv", "text/csv")
        
# ==============================================================================
# MÓDULO DE CARGA DIRECTA A GITHUB (ÚNICA FUENTE DE VERDAD, SIN DISCO LOCAL)
# ==============================================================================
# Todas las funciones de esta sección leen y escriben ÚNICAMENTE contra la API
# de GitHub. No hay lectura ni escritura de disco local: cada carga deja al
# repositorio como el estado completo y definitivo del sistema.
# ------------------------------------------------------------------------------

def _headers_github_contenido() -> Dict[str, str]:
    """Headers estándar para llamadas a la API de contenidos/objetos de GitHub."""
    h = dict(HEADERS)
    h["Accept"] = "application/vnd.github.v3+json"
    return h


@st.cache_data(ttl=180, show_spinner=False)
def listar_archivos_semanales_github() -> List[str]:
    """Lista los archivos CSV existentes en la carpeta configurada del repositorio."""
    try:
        resp = requests.get(GITHUB_API_URL, headers=_headers_github_contenido(), timeout=15)
        if resp.status_code == 200:
            return sorted([
                f["name"] for f in resp.json()
                if isinstance(f, dict) and f["name"].lower().endswith(".csv")
            ])
    except Exception as e:
        logger.error(f"Error listando archivos en GitHub: {e}")
    return []


def _push_contenido_api(ruta_relativa: str, contenido_bytes: bytes, mensaje: str) -> Tuple[bool, str]:
    """
    Sube/actualiza un archivo PEQUEÑO (<1MB) usando la API de Contenidos de GitHub.
    Adecuado para los CSV semanales individuales.
    """
    try:
        url_api = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{ruta_relativa}"
        res_check = requests.get(f"{url_api}?ref={GITHUB_BRANCH}", headers=_headers_github_contenido(), timeout=15)
        sha_actual = res_check.json().get("sha") if res_check.status_code == 200 else None

        payload = {
            "message": mensaje,
            "content": base64.b64encode(contenido_bytes).decode("utf-8"),
            "branch": GITHUB_BRANCH
        }
        if sha_actual:
            payload["sha"] = sha_actual

        r = requests.put(url_api, json=payload, headers=_headers_github_contenido(), timeout=30)
        if r.status_code in (200, 201):
            return True, "OK"
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, str(e)


def _push_blob_git_data_api(ruta_relativa: str, contenido_bytes: bytes, mensaje: str) -> Tuple[bool, str]:
    """
    Sube/actualiza un archivo de CUALQUIER TAMAÑO (hasta ~100MB) usando la Git Data API
    (blob + tree + commit + actualización de referencia de rama). Es necesario para
    'datos_consolidados.parquet': la API de Contenidos simple está limitada a ~1MB y un
    consolidado de varias semanas de operación la supera con facilidad.
    """
    base_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}"
    headers = _headers_github_contenido()
    try:
        # 1. Crear el blob con el contenido binario
        r_blob = requests.post(
            f"{base_url}/git/blobs",
            json={"content": base64.b64encode(contenido_bytes).decode("utf-8"), "encoding": "base64"},
            headers=headers, timeout=60
        )
        if r_blob.status_code not in (200, 201):
            return False, f"Error creando blob: {r_blob.text[:300]}"
        sha_blob = r_blob.json()["sha"]

        # 2. Referencia y commit actuales de la rama
        r_ref = requests.get(f"{base_url}/git/ref/heads/{GITHUB_BRANCH}", headers=headers, timeout=20)
        if r_ref.status_code != 200:
            return False, f"Error obteniendo referencia de rama: {r_ref.text[:300]}"
        sha_commit_actual = r_ref.json()["object"]["sha"]

        r_commit = requests.get(f"{base_url}/git/commits/{sha_commit_actual}", headers=headers, timeout=20)
        if r_commit.status_code != 200:
            return False, f"Error obteniendo commit actual: {r_commit.text[:300]}"
        sha_tree_actual = r_commit.json()["tree"]["sha"]

        # 3. Nuevo árbol con el archivo actualizado
        r_tree = requests.post(
            f"{base_url}/git/trees",
            json={"base_tree": sha_tree_actual, "tree": [{
                "path": ruta_relativa, "mode": "100644", "type": "blob", "sha": sha_blob
            }]},
            headers=headers, timeout=30
        )
        if r_tree.status_code not in (200, 201):
            return False, f"Error creando árbol: {r_tree.text[:300]}"
        sha_tree_nuevo = r_tree.json()["sha"]

        # 4. Nuevo commit
        r_new_commit = requests.post(
            f"{base_url}/git/commits",
            json={"message": mensaje, "tree": sha_tree_nuevo, "parents": [sha_commit_actual]},
            headers=headers, timeout=30
        )
        if r_new_commit.status_code not in (200, 201):
            return False, f"Error creando commit: {r_new_commit.text[:300]}"
        sha_new_commit = r_new_commit.json()["sha"]

        # 5. Mover la rama al nuevo commit
        r_update_ref = requests.patch(
            f"{base_url}/git/refs/heads/{GITHUB_BRANCH}",
            json={"sha": sha_new_commit}, headers=headers, timeout=20
        )
        if r_update_ref.status_code in (200, 201):
            return True, "OK"
        return False, f"Error actualizando rama: {r_update_ref.text[:300]}"
    except Exception as e:
        return False, str(e)


@st.cache_data(ttl=600, show_spinner=False, max_entries=3)
def interpretar_csv_pegado(texto: str, separador: str = "Automático") -> pd.DataFrame:
    """Procesa el texto del portapapeles; conserva identificadores y celdas vacías."""
    texto = texto.lstrip("\ufeff").strip("\r\n")
    if not texto.strip():
        raise ValueError("Pega los encabezados y al menos una fila de datos.")
    delimitadores = {"Coma (,)": ",", "Punto y coma (;)": ";", "Tabulación (Excel)": "\t"}
    sep = delimitadores.get(separador)
    if sep is None:
        try:
            sep = csv.Sniffer().sniff(texto[:65536], delimiters=",;\t").delimiter
        except csv.Error:
            raise ValueError("No se detectó el separador. Selecciona coma, punto y coma o tabulación.")
    lector = csv.reader(io.StringIO(texto), delimiter=sep, strict=True)
    encabezados = next(lector)
    encabezados = [c.strip() for c in encabezados]
    if len(encabezados) < 2 or any(not c for c in encabezados) or len(set(encabezados)) != len(encabezados):
        raise ValueError("Los encabezados deben ser únicos, sin celdas vacías y con al menos dos columnas.")
    filas = 0
    for fila in lector:
        if not fila:
            continue
        if len(fila) != len(encabezados):
            raise ValueError(f"La línea {lector.line_num} tiene {len(fila)} columnas; se esperaban {len(encabezados)}.")
        filas += 1
    if not filas:
        raise ValueError("El texto solo contiene encabezados; faltan los registros.")
    df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False)
    df.columns = encabezados
    return df


def _parsear_texto_pegado(contenido_txt: str) -> Optional[pd.DataFrame]:
    try:
        return interpretar_csv_pegado(contenido_txt)
    except (ValueError, csv.Error, pd.errors.ParserError):
        return None


def leer_bytes_github(ruta: str) -> bytes:
    """Lee la revisión actual por SHA, sin depender de la caché del dominio raw."""
    meta = requests.get(url_contenido_github(ruta), params={"ref": GITHUB_BRANCH}, headers=_headers_github_contenido(), timeout=25)
    meta.raise_for_status()
    sha = meta.json()["sha"]
    res = requests.get(f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/git/blobs/{sha}",
        headers={**_headers_github_contenido(), "Accept": "application/vnd.github.raw+json"}, timeout=45)
    res.raise_for_status()
    return res.content


def _regenerar_parquet_consolidado(nombre_csv_afectado: str, df_raw_final_archivo: pd.DataFrame) -> pd.DataFrame:
    """Conserva el histórico; si falta el consolidado, reconstruye todas las fuentes."""
    inventario_repositorio_github.clear()
    inventario = inventario_repositorio_github()
    prefijo = f"{GITHUB_FOLDER.strip('/')}/" if GITHUB_FOLDER.strip('/') else ""
    ruta_consolidado = prefijo + "datos_consolidados.parquet"
    nuevo = df_raw_final_archivo.copy()
    nuevo["Archivo_Origen"] = nombre_csv_afectado
    if ruta_consolidado in inventario:
        anterior = pd.read_parquet(io.BytesIO(leer_bytes_github(ruta_consolidado)))
        if "Archivo_Origen" not in anterior.columns:
            raise ValueError("No se puede identificar el histórico en el consolidado actual.")
        anterior = anterior[anterior["Archivo_Origen"] != nombre_csv_afectado]
        return pd.concat([anterior, transformar_dataset_completo(nuevo)], ignore_index=True)
    fuentes = [nuevo]
    for ruta, tipo in inventario.items():
        nombre = ruta[len(prefijo):] if ruta.startswith(prefijo) else ""
        if tipo != "blob" or not nombre or "/" in nombre or nombre in (nombre_csv_afectado, "datos_consolidados.parquet"):
            continue
        if not nombre.lower().endswith((".csv", ".parquet")):
            continue
        contenido = leer_bytes_github(ruta)
        if nombre.lower().endswith(".parquet"):
            df = pd.read_parquet(io.BytesIO(contenido))
        else:
            try:
                texto = contenido.decode("utf-8-sig")
            except UnicodeDecodeError:
                texto = contenido.decode("latin1")
            df = interpretar_csv_pegado(texto)
        df["Archivo_Origen"] = nombre
        fuentes.append(df)
    return transformar_dataset_completo(pd.concat(fuentes, ignore_index=True))


def validar_ruta_carpeta(ruta: str) -> str:
    ruta = ruta.strip()
    if not ruta or ruta.startswith("/") or "\\" in ruta:
        raise ValueError("Escribe una ruta relativa, por ejemplo datos_semanales/2026.")
    if any(p in ("", ".", "..", ".git") for p in ruta.split("/")) or any(ord(c) < 32 for c in ruta):
        raise ValueError("La carpeta contiene segmentos vacíos o caracteres no válidos.")
    return ruta


@st.cache_data(ttl=180, show_spinner=False)
def inventario_repositorio_github() -> Dict[str, str]:
    """Rutas y tipos reales del repositorio, incluidas las subcarpetas."""
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/git/trees/{quote(GITHUB_BRANCH, safe='')}"
    resp = requests.get(url, params={"recursive": "1"}, headers=_headers_github_contenido(), timeout=25)
    resp.raise_for_status()
    datos = resp.json()
    if datos.get("truncated"):
        raise ValueError("GitHub devolvió un listado incompleto. No se puede seleccionar un destino con este listado.")
    if not isinstance(datos.get("tree"), list):
        raise ValueError("GitHub no devolvió el listado de carpetas.")
    return {item["path"]: item["type"] for item in datos["tree"]}


def url_contenido_github(ruta: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{quote(ruta, safe='/')}"


def guardar_csv_en_carpeta(ruta: str, df: pd.DataFrame, agregar: bool) -> pd.DataFrame:
    """Lee y guarda sobre la misma revisión; no sobrescribe destinos nuevos existentes."""
    url = url_contenido_github(ruta)
    res = requests.get(url, params={"ref": GITHUB_BRANCH}, headers=_headers_github_contenido(), timeout=25)
    sha = None
    final = df.copy()
    if agregar:
        res.raise_for_status()
        metadata = res.json()
        if metadata.get("type") != "file":
            raise ValueError("El destino no es un archivo CSV.")
        sha = metadata["sha"]
        # Leer por blob SHA garantiza que el contenido corresponda a la revisión leída.
        raw = requests.get(
            f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/git/blobs/{sha}",
            headers={**_headers_github_contenido(), "Accept": "application/vnd.github.raw+json"}, timeout=45,
        )
        raw.raise_for_status()
        try:
            texto = raw.content.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = raw.content.decode("latin1")
        previo = interpretar_csv_pegado(texto)
        if set(previo.columns) != set(df.columns):
            raise ValueError("Las columnas pegadas no coinciden con las del archivo elegido. No se guardaron cambios.")
        final = pd.concat([previo, df[previo.columns]], ignore_index=True)
        aliases = [LISTA_ALIAS_ORDEN, LISTA_ALIAS_CUENTA, LISTA_ALIAS_OT, LISTA_ALIAS_TIPO]
        claves = [detectar_columna_por_patrones(list(final.columns), alias) for alias in aliases]
        if all(claves) and final[claves].apply(lambda c: c.str.strip().ne("")).all().all():
            final = final.drop_duplicates(subset=claves, keep="last")
        else:
            # Sin clave completa solo se eliminan filas idénticas.
            final = final.drop_duplicates(keep="last")
    elif res.status_code == 200:
        raise ValueError("Ya existe un archivo con ese nombre. Selecciona actualizar o escribe otro nombre.")
    elif res.status_code != 404:
        res.raise_for_status()
    contenido_csv = final.to_csv(index=False).encode("utf-8-sig")
    payload = {
        "message": f"Actualizar captura CSV: {ruta}",
        "content": base64.b64encode(contenido_csv).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    resultado = requests.put(url, json=payload, headers=_headers_github_contenido(), timeout=60)
    if resultado.status_code == 409:
        raise ValueError("El archivo cambió durante la carga. Actualiza el listado y vuelve a intentarlo.")
    resultado.raise_for_status()
    # Confirmar la ruta y el contenido de la revisión escrita antes del mensaje verde.
    try:
        datos = resultado.json()
        commit = datos["commit"]["sha"]
        esperado = hashlib.sha1(b"blob " + str(len(contenido_csv)).encode("ascii") + b"\0" + contenido_csv).hexdigest()
        if datos["content"]["sha"] != esperado or datos["content"]["path"] != ruta:
            raise ValueError("La respuesta no coincide con el CSV enviado.")
        comprobacion = requests.get(url, params={"ref": commit}, headers=_headers_github_contenido(), timeout=25)
        comprobacion.raise_for_status()
        confirmado = comprobacion.json()
        if confirmado.get("sha") != esperado or confirmado.get("path") != ruta:
            raise ValueError("No coincide la lectura del archivo guardado.")
    except Exception as exc:
        raise ValueError("GitHub aceptó la escritura, pero no se pudo verificar el archivo. Revisa la carpeta en GitHub antes de intentar guardarlo de nuevo.") from exc
    final.attrs["guardado_github"] = {
        "ruta": ruta,
        "filas": len(final),
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "url": f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/blob/{commit}/{quote(ruta, safe='/')}",
    }
    return final


def abrir_captura_completa():
    st.session_state["seccion_principal"] = "📝 Capturar / actualizar datos"


def renderizar_modulo_carga_github():
    # Contraste del formulario independiente del tema oscuro del dashboard.
    st.markdown("""
    <style>
    .st-key-formulario_carga,
    .st-key-formulario_carga * {
        color: #000000 !important;
    }
    .st-key-formulario_carga [data-testid="stWidgetLabel"] *,
    .st-key-formulario_carga [data-testid="stCaptionContainer"] * {
        color: #000000 !important;
        opacity: 1 !important;
    }
    .st-key-formulario_carga div[data-testid="stTextArea"] textarea,
    .st-key-formulario_carga div[data-testid="stTextInput"] input,
    .st-key-formulario_carga [data-baseweb="input"],
    .st-key-formulario_carga [data-baseweb="base-input"],
    .st-key-formulario_carga [data-baseweb="select"] > div,
    .st-key-formulario_carga div.stButton > button {
        background-color: #ffffff !important;
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        border-color: #64748b !important;
        caret-color: #000000 !important;
    }
    .st-key-formulario_carga textarea::placeholder,
    .st-key-formulario_carga input::placeholder {
        color: #404040 !important;
        -webkit-text-fill-color: #404040 !important;
        opacity: 1 !important;
    }
    .st-key-formulario_carga div.stButton > button:hover {
        background-color: #e2e8f0 !important;
    }
    .st-key-formulario_carga div.stButton > button:disabled {
        background-color: #e5e7eb !important;
        opacity: 0.65 !important;
    }
    /* Cubrir también las capas internas de los selectores nativos de Streamlit. */
    .st-key-formulario_carga [data-testid="stSelectbox"] div,
    .st-key-formulario_carga [data-testid="stSelectbox"] input,
    .st-key-formulario_carga [data-testid="stSelectbox"] button,
    .st-key-formulario_carga [data-testid="stSelectbox"] select,
    .st-key-formulario_carga [data-testid="stSelectbox"] [role="combobox"],
    .st-key-formulario_carga [data-testid="stTextInput"] button {
        background: #ffffff !important;
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
    }
    .st-key-formulario_carga [data-testid="stSelectbox"] svg,
    .st-key-formulario_carga [data-testid="stTextInput"] button svg {
        color: #000000 !important;
        fill: #000000 !important;
    }
    body:has(.st-key-formulario_carga) [role="listbox"],
    body:has(.st-key-formulario_carga) [role="listbox"] * {
        background: #ffffff !important;
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
    }
    /* Los menús desplegados se montan fuera del contenedor del formulario. */
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="listbox"],
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="option"] {
        background-color: #ffffff !important;
        color: #000000 !important;
    }
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="option"] * {
        color: #000000 !important;
    }
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="option"]:hover {
        background-color: #e2e8f0 !important;
    }
    </style>
    """, unsafe_allow_html=True)
    with st.container(key="formulario_carga"):
        _renderizar_formulario_carga_github()


def _renderizar_formulario_carga_github():
    st.markdown("### 📋 Pegar información y guardar en GitHub")
    st.caption(f"Versión {VERSION_SISTEMA} · Repositorio: {GITHUB_USER}/{GITHUB_REPO} · Rama: {GITHUB_BRANCH}")
    resultado_previo = st.session_state.get("resultado_captura")
    if isinstance(resultado_previo, dict):
        st.success(f"Último guardado verificado: {resultado_previo['ruta']} · {resultado_previo['filas']:,} registros · {resultado_previo['fecha']}")
        st.link_button("Abrir el CSV guardado en GitHub", resultado_previo["url"])
    # Sin columnas ni pestañas envolventes: ocupa todo el ancho del área principal.
    contenido_txt = st.text_area(
        "Pega aquí el CSV completo o las celdas copiadas desde Excel, incluidos los encabezados",
        height=380, key="contenido_txt_carga_v2",
        placeholder='Cuenta,OS,OT,Tipo,Fecha creacion FFM\n00123,0009,002,Soporte,16/09/2026',
        help="Usa Ctrl+V o Cmd+V. Se guardan los datos como CSV; colores, fuentes y estilos del portapapeles no se almacenan.",
    )
    separador = st.selectbox("Separador del texto", ["Automático", "Coma (,)", "Punto y coma (;)", "Tabulación (Excel)"], key="separador_captura")
    df_preview = None
    if contenido_txt.strip():
        try:
            df_preview = interpretar_csv_pegado(contenido_txt, separador)
        except Exception as exc:
            st.error(f"Revisa el texto pegado: {exc}")
    if df_preview is not None:
        st.caption(f"Vista previa: {len(df_preview):,} registros · {len(df_preview.columns)} columnas")
        st.dataframe(df_preview.head(20), width="stretch", hide_index=True)

    st.markdown("#### Carpeta de destino")
    if st.button("🔄 Actualizar listado de carpetas", key="actualizar_carpetas"):
        inventario_repositorio_github.clear()
    inventario = None
    try:
        inventario = inventario_repositorio_github()
    except Exception:
        st.error("No se pudo consultar el repositorio. Revisa la conexión, la rama y el token de GitHub; después actualiza el listado.")

    carpeta = None
    nombre_csv = None
    agregar = False
    if inventario is not None:
        carpetas = [""] + sorted(r for r, tipo in inventario.items() if tipo == "tree")
        accion_carpeta = st.radio("Destino", ["Usar carpeta existente", "Crear carpeta nueva"], horizontal=True, key="accion_carpeta")
        if accion_carpeta == "Usar carpeta existente":
            carpeta = st.selectbox("Carpetas del repositorio", carpetas,
                index=carpetas.index(GITHUB_FOLDER) if GITHUB_FOLDER in carpetas else 0,
                format_func=lambda p: p or "/ (raíz del repositorio)", key="carpeta_existente")
        else:
            padre = st.selectbox("Crear dentro de", carpetas,
                format_func=lambda p: p or "/ (raíz del repositorio)", key="carpeta_padre")
            nueva = st.text_input("Nombre de la nueva carpeta", key="carpeta_nueva", placeholder="cierres_2026/semana_38")
            if nueva.strip():
                try:
                    ruta_nueva = validar_ruta_carpeta(nueva)
                    propuesta = f"{padre}/{ruta_nueva}" if padre else ruta_nueva
                    if propuesta in inventario:
                        raise ValueError("Esa ruta ya existe. Elígela como carpeta existente o usa otro nombre.")
                    partes = propuesta.split("/")
                    if any(inventario.get("/".join(partes[:i])) not in (None, "tree") for i in range(1, len(partes))):
                        raise ValueError("Una parte de la ruta corresponde a un archivo, no a una carpeta.")
                    carpeta = propuesta
                except ValueError as exc:
                    st.error(str(exc))
            st.caption("La carpeta nueva se creará al guardar el primer CSV.")
        if carpeta is not None:
            prefijo = f"{carpeta}/" if carpeta else ""
            archivos = sorted(r[len(prefijo):] for r, tipo in inventario.items()
                if tipo == "blob" and r.startswith(prefijo) and "/" not in r[len(prefijo):] and r.lower().endswith(".csv"))
            modos = ["Crear un CSV nuevo"] + (["Actualizar un CSV existente"] if archivos else [])
            modo = st.radio("Archivo de destino", modos, horizontal=True, key=f"modo_csv:{carpeta}")
            agregar = modo == "Actualizar un CSV existente"
            if agregar:
                nombre_csv = st.selectbox("CSV que recibirá las actualizaciones", archivos, key=f"csv_existente:{carpeta}")
                st.caption("Se conservan los registros anteriores y se actualizan las coincidencias por OS, Cuenta, OT y Tipo.")
            else:
                nombre = st.text_input("Nombre del CSV", key="nombre_csv_captura", placeholder="CIERRE DIARIO SEM 38 2026.csv").strip()
                if nombre:
                    if "/" in nombre or "\\" in nombre or nombre in (".", "..") or any(ord(c) < 32 for c in nombre):
                        st.error("Escribe solo el nombre del archivo; la carpeta se selecciona arriba.")
                    else:
                        candidato = nombre if nombre.lower().endswith(".csv") else f"{nombre}.csv"
                        if prefijo + candidato in inventario:
                            st.error("Ese nombre ya existe. Elige actualizar el CSV o escribe otro nombre.")
                        else:
                            nombre_csv = candidato
    destino = f"{carpeta}/{nombre_csv}" if carpeta else nombre_csv
    if carpeta is not None and nombre_csv:
        st.info(f"Destino seleccionado: {GITHUB_REPO}/{destino}")
        st.caption("Esta selección todavía no carga los datos. Pulsa el botón Guardar CSV y espera la confirmación verde.")
        if carpeta != GITHUB_FOLDER.strip("/"):
            st.caption(f"El dashboard actual consulta la carpeta {GITHUB_FOLDER}; este CSV se guardará en el destino que elegiste.")
    clave = st.text_input("Clave de autorización del portal", type="password", key="token_auth_carga_v2")
    puede_guardar = df_preview is not None and carpeta is not None and bool(nombre_csv)
    if st.button("💾 Guardar CSV en la carpeta seleccionada", type="primary", disabled=not puede_guardar, key="guardar_captura"):
        st.session_state.pop("resultado_captura", None)
        if not GITHUB_TOKEN:
            st.error("Configura github.token en los secretos de Streamlit para guardar en el repositorio.")
            return
        if clave != st.secrets.get("UPLOAD_PASSWORD", "admin123"):
            st.error("Clave de autorización incorrecta.")
            return
        with st.spinner("Guardando el CSV en la carpeta seleccionada..."):
            try:
                final = guardar_csv_en_carpeta(destino, df_preview, agregar)
            except Exception as exc:
                st.error(f"No se confirmó el guardado: {exc}")
                return
        mensaje = f"✅ Guardado y verificado en GitHub: {destino} · {len(final):,} registros en el CSV."
        confirmado = final.attrs["guardado_github"]
        st.session_state["resultado_captura"] = confirmado
        st.success(mensaje)
        st.link_button("Abrir el CSV guardado en GitHub", confirmado["url"])
        st.cache_data.clear()
        # El consolidado pertenece únicamente a la carpeta configurada del dashboard.
        if carpeta == GITHUB_FOLDER.strip("/"):
            try:
                consolidado = _regenerar_parquet_consolidado(nombre_csv, final)
                if consolidado is None or consolidado.empty:
                    raise ValueError("El consolidado no contiene datos válidos.")
                buf = io.BytesIO()
                consolidado.to_parquet(buf, index=False)
                ruta_parquet = "/".join(p for p in (GITHUB_FOLDER.strip("/"), "datos_consolidados.parquet") if p)
                ok, detalle = _push_blob_git_data_api(ruta_parquet, buf.getvalue(), f"Actualizar consolidado tras {nombre_csv}")
                if not ok:
                    raise ValueError(detalle)
                st.success("También se actualizó el consolidado del dashboard.")
            except Exception:
                st.warning("El CSV sí quedó guardado, pero no se actualizó el consolidado. El dashboard puede seguir mostrando la versión anterior.")


@st.dialog("Detalle Ampliado de Reincidencia por Usuario", width="large")
def mostrar_modal_detalle_usuario(df_usuario: pd.DataFrame, usuario_nom: str):
    st.markdown(f"### Historial de Reincidencias Provocadas por: **{usuario_nom}**")
    
    if df_usuario.empty:
        st.info("No se encontraron folios reincidentes para este usuario.")
        return

    df_modal = df_usuario.copy()
    if "TIPO_2" not in df_modal.columns:
        df_modal["TIPO_2"] = df_modal.get("Causa_Origen", "N/A")

    cols_popup = ["FOLIO_KEY", "Cuenta_Cliente", "Num_Semana_Archivo", "TIPO_2", "Falla_Nueva", "Empresa_Origen_Reincidencia"]
    cols_presentes = [c for c in cols_popup if c in df_modal.columns]
    
    nombres_popup = {
        "FOLIO_KEY": "Folio Reincidente",
        "Cuenta_Cliente": "Cuenta Cliente",
        "Num_Semana_Archivo": "Semana Reincidencia",
        "TIPO_2": "Tipo / Causa Origen (TIPO 2)",
        "Falla_Nueva": "Falla Reportada (Soporte)",
        "Empresa_Origen_Reincidencia": "Empresa Técnico"
    }
    
    df_mostrar_modal = df_modal[cols_presentes].rename(columns=nombres_popup)
    st.dataframe(df_mostrar_modal, width="stretch", hide_index=True, height=400)
    
    csv_popup = df_mostrar_modal.to_csv(index=False).encode('utf-8')
    st.download_button(
        label=f"Descargar Historial de {usuario_nom} (CSV)",
        data=csv_popup,
        file_name=f"Reincidencias_{usuario_nom}.csv",
        mime="text/csv",
        width="stretch"
    )


def renderizar_pestana_reincidencias_total(df_folios: pd.DataFrame, dimension_sel: str) -> None:
    st.markdown("### Módulo Avanzado de Reincidencias y Control de Efectividad")
    
    if df_folios.empty:
        st.warning("⚠️ No se encontraron registros con los filtros seleccionados.")
        return

    for col_req in ["Empresa_Origen_Reincidencia", "Usuario_Origen_Reincidencia", "Causa_Origen", "TIPO_2", "Falla_Nueva", "ES_CASO_ESPECIAL"]:
        if col_req not in df_folios.columns:
            df_folios[col_req] = df_folios["Causa_Origen"] if col_req == "TIPO_2" else "N/A"

    df_rein_base = df_folios[df_folios["ES_REINCIDENCIA"] == "SI"]

    st.markdown("#### Filtros Avanzados de Reincidencias")
    col_f0, col_f1, col_f2, col_f3, col_f4 = st.columns(5)

    with col_f0:
        st.markdown("**📍 Distrito**")
        distritos_rein = sorted([x for x in df_rein_base["Distrito"].unique() if str(x).upper() not in ["N/A", "NAN", "NONE", ""]]) if "Distrito" in df_rein_base.columns else []
        f_dist_rein = st.multiselect("Filtrar Distrito:", distritos_rein, key="fltr_dist_rein", label_visibility="collapsed")

    with col_f1:
        st.markdown("**🏢 Compañía Reincidente**")
        empresas_rein = sorted([x for x in df_rein_base["Empresa_Origen_Reincidencia"].unique() if str(x).upper() not in ["N/A", "NAN", "NONE", ""]])
        f_emp_rein = st.multiselect("Filtrar Empresa:", empresas_rein, key="fltr_emp_rein", label_visibility="collapsed")

    with col_f2:
        st.markdown("**👤 Técnico Reincidente**")
        techs_rein = sorted([x for x in df_rein_base["Usuario_Origen_Reincidencia"].unique() if str(x).upper() not in ["N/A", "NAN", "NONE", ""]])
        f_tech_rein = st.multiselect("Filtrar Técnico:", techs_rein, key="fltr_tech_rein", label_visibility="collapsed")

    with col_f3:
        st.markdown("**📋 Tipo / Causa Origen (TIPO 2)**")
        tipos2_rein = sorted([x for x in df_rein_base["TIPO_2"].unique() if str(x).upper() not in ["N/A", "NAN", "NONE", ""]])
        f_tipo2_rein = st.multiselect("Filtrar TIPO 2:", tipos2_rein, key="fltr_tipo2_rein", label_visibility="collapsed")

    with col_f4:
        st.markdown("**🛠️ Falla Nueva (Soporte)**")
        fallas_nuevas = sorted([x for x in df_rein_base["Falla_Nueva"].unique() if str(x).upper() not in ["N/A", "NAN", "NONE", ""]])
        f_falla_nueva = st.multiselect("Filtrar Falla Nueva:", fallas_nuevas, key="fltr_falla_rein", label_visibility="collapsed")

    df_filtrado_rein = df_rein_base
    if f_dist_rein:
        df_filtrado_rein = df_filtrado_rein[df_filtrado_rein["Distrito"].isin(f_dist_rein)]
    if f_emp_rein:
        df_filtrado_rein = df_filtrado_rein[df_filtrado_rein["Empresa_Origen_Reincidencia"].isin(f_emp_rein)]
    if f_tech_rein:
        df_filtrado_rein = df_filtrado_rein[df_filtrado_rein["Usuario_Origen_Reincidencia"].isin(f_tech_rein)]
    if f_tipo2_rein:
        df_filtrado_rein = df_filtrado_rein[df_filtrado_rein["TIPO_2"].isin(f_tipo2_rein)]
    if f_falla_nueva:
        df_filtrado_rein = df_filtrado_rein[df_filtrado_rein["Falla_Nueva"].isin(f_falla_nueva)]

    patron_efectividad = r"INSTALA|SOPORTE|CAMBIO.*DOMICILIO|CAMBIO.*EQUIPO"
    col_tipo_base = "Tipo_Orden" if "Tipo_Orden" in df_folios.columns else "TIPO"
    mask_base_efectividad = df_folios[col_tipo_base].astype(str).str.upper().str.contains(patron_efectividad, regex=True, na=False)
    df_base_efectividad = df_folios[mask_base_efectividad]

    total_base_evaluados = len(df_base_efectividad)
    total_reincidentes_filtrados = len(df_filtrado_rein)
    tasa_efectividad = round(((total_base_evaluados - total_reincidentes_filtrados) / total_base_evaluados * 100), 2) if total_base_evaluados > 0 else 100.0

    st.markdown(f"""
        <div class="kpi-wrapper-grid" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;">
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">EVENTOS BASE (EFECTIVIDAD)</div>
                <div class="kpi-card-value">{total_base_evaluados:,}</div>
                <div class="kpi-card-subtitle">Inst / Sop / C. Dom / C. Eq</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">TOTAL SOPORTES REINCIDENTES</div>
                <div class="kpi-card-value" style="color:{PALETA_COLOR["naranja_desierto"]} !important;">{total_reincidentes_filtrados:,}</div>
                <div class="kpi-card-subtitle">Visitas N-1 Identificadas</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">EFECTIVIDAD OPERATIVA GENERAL</div>
                <div class="kpi-card-value" style="color:{PALETA_COLOR["verde_montana"]} !important;">{tasa_efectividad}%</div>
                <div class="kpi-card-subtitle">% Eventos Sin Reincidencia</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">CASOS ESPECIALES DETECTADOS</div>
                <div class="kpi-card-value">{(df_folios["ES_CASO_ESPECIAL"] == "SI").sum():,}</div>
                <div class="kpi-card-subtitle">Secuencias Especiales</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="matrix-title-card" style="background:#1e293b; padding:12px; border-radius:8px; margin:16px 0 12px 0; border:1px solid #334155;">
            <b style="color:#f8fafc; font-size:15px;">📊 EVALUACIÓN DE EFECTIVIDAD Y REINCIDENCIA POR TÉCNICO ORIGEN</b>
            <p style="color:#94a3b8; margin:2px 0 0 0; font-size:12px;">Consolidado total único por Técnico (Eventos Atendidos vs Reincidencias Provocadas).</p>
        </div>
    """, unsafe_allow_html=True)

    if not df_filtrado_rein.empty:
        col_tech_base = detectar_columna_por_patrones(list(df_folios.columns), ["usuario_tecnico", "tecnico", "tech", "usuario", "atendio"]) or "Usuario_Tecnico"
        eventos_por_tech = df_base_efectividad.groupby(col_tech_base, observed=True).size().to_dict()

        # Concatenación blindada contra tipos de datos mixtos o nulos en agregaciones
        df_agrupado_tech = df_filtrado_rein.groupby(
            ["Usuario_Origen_Reincidencia", "Empresa_Origen_Reincidencia"], observed=True
        ).agg(
            Total_Reincidencias=("FOLIO_KEY", "count"),
            Causas_TIPO_2=("TIPO_2", lambda x: " | ".join(sorted(set(str(v).strip() for v in x if pd.notna(v) and str(v).strip() not in ["", "nan", "None"])))),
            Fallas_Nuevas=("Falla_Nueva", lambda x: " | ".join(sorted(set(str(v).strip() for v in x if pd.notna(v) and str(v).strip() not in ["", "nan", "None"]))))
        ).reset_index()

        df_agrupado_tech["Eventos_Atendidos"] = df_agrupado_tech["Usuario_Origen_Reincidencia"].map(eventos_por_tech).fillna(df_agrupado_tech["Total_Reincidencias"])
        df_agrupado_tech["Eventos_Atendidos"] = np.maximum(df_agrupado_tech["Eventos_Atendidos"], df_agrupado_tech["Total_Reincidencias"])

        atendidos = df_agrupado_tech["Eventos_Atendidos"].to_numpy()
        reincidencias = df_agrupado_tech["Total_Reincidencias"].to_numpy()
        
        df_agrupado_tech["Efectividad_%"] = np.where(
            atendidos > 0, 
            np.round(((atendidos - reincidencias) / atendidos) * 100, 2), 
            0.0
        )

        df_agrupado_tech = df_agrupado_tech.rename(columns={
            "Usuario_Origen_Reincidencia": "Técnico Reincidente (Origen)",
            "Empresa_Origen_Reincidencia": "Empresa",
            "Eventos_Atendidos": "Eventos Completados (Inst/Sop/Dom/Eq)",
            "Total_Reincidencias": "Total Reincidencias",
            "Efectividad_%": "% Efectividad Operativa"
        }).sort_values(by="Total Reincidencias", ascending=False)

        cols_orden = [
            "Técnico Reincidente (Origen)", "Empresa", "Eventos Completados (Inst/Sop/Dom/Eq)", 
            "Total Reincidencias", "% Efectividad Operativa", "Causas_TIPO_2", "Fallas_Nuevas"
        ]
        df_agrupado_tech = df_agrupado_tech[cols_orden]

        col_t1, col_t2 = st.columns([0.75, 0.25])
        with col_t1:
            st.dataframe(
                df_agrupado_tech, 
                width="stretch", 
                hide_index=True, 
                height=380,
                column_config={"% Efectividad Operativa": st.column_config.NumberColumn(format="%.2f %%")}
            )

        with col_t2:
            st.markdown("**Ver Detalle Ampliado en Popup:**")
            tech_lista = sorted(df_agrupado_tech["Técnico Reincidente (Origen)"].unique())
            tech_seleccionado = st.selectbox("Seleccionar Técnico:", tech_lista, key="sb_pop_tech")
            
            if st.button("Abrir Detalle Pop-Up", width="stretch", key="btn_pop_tech"):
                sub_df = df_filtrado_rein[df_filtrado_rein["Usuario_Origen_Reincidencia"] == tech_seleccionado]
                mostrar_modal_detalle_usuario(sub_df, tech_seleccionado)
    else:
        st.info("No hay datos de reincidencias para mostrar con los filtros aplicados.")

    st.markdown("---")

    st.markdown("""
        <div class="matrix-title-card" style="background:#1e293b; padding:12px; border-radius:8px; margin-bottom:12px; border:1px solid #334155;">
            <b style="color:#f8fafc; font-size:15px;">📜 HISTORIAL OPERATIVO POR CUENTA DE CLIENTE</b>
            <p style="color:#94a3b8; margin:2px 0 0 0; font-size:12px;">Desglose de visitas por Cuenta de Cliente reincidente.</p>
        </div>
    """, unsafe_allow_html=True)

    if not df_filtrado_rein.empty:
        df_agrupado_cuenta = df_filtrado_rein.groupby("Cuenta_Cliente", observed=True).agg(
            Visitas_Totales=("FOLIO_KEY", "count"),
            Semanas_Con_Incidencia=("Num_Semana_Archivo", lambda x: ", ".join(map(str, sorted(set(x))))),
            Causas_Historicas=("TIPO_2", lambda x: " | ".join(sorted(set(str(v).strip() for v in x if pd.notna(v) and str(v).strip() != "")))),
            Fallas_Reportadas=("Falla_Nueva", lambda x: " | ".join(sorted(set(str(v).strip() for v in x if pd.notna(v) and str(v).strip() != "")))),
            Tecnicos_Involucrados=("Usuario_Origen_Reincidencia", lambda x: " | ".join(sorted(set(str(v).strip() for v in x if pd.notna(v) and str(v).strip() != ""))))
        ).reset_index().sort_values(by="Visitas_Totales", ascending=False)

        st.dataframe(df_agrupado_cuenta, width="stretch", hide_index=True, height=350)
    else:
        st.info("No hay historial de cuentas con reincidencia para mostrar.")


# ==============================================================================
# 3. ESTILOS CSS UNIFICADOS (MODO CLARO CONTROLADO CON BORDES ENTERPRISE)
# ==============================================================================

def inyectar_estilos_css_enterprise() -> None:
    # Verificación de seguridad para evitar fallos si no existe la paleta
    paleta = globals().get("PALETA_COLOR", {
        "azul_noche": "#0B192C",
        "azul_marina": "#1E3E62",
        "turquesa_cyan": "#008080",
        "naranja_desierto": "#FF6500"
    })

    css_custom = f"""
    <style>
    @import url("https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap");

    html, body, [class*="css"], .stApp {{
        font-family: "Plus Jakarta Sans", -apple-system, sans-serif !important;
        background-color: #F8FAFC !important;
        color: #000000 !important;
    }}

    [data-testid="stSidebar"] {{
        background-color: {paleta["azul_noche"]} !important;
        min-width: 320px !important;
    }}
    [data-testid="stSidebar"] * {{
        color: #FFFFFF !important;
    }}

    .main-header-enterprise {{
        background: linear-gradient(135deg, {paleta["azul_noche"]} 0%, {paleta["azul_marina"]} 100%);
        padding: 24px 30px;
        border-radius: 14px;
        border-bottom: 4px solid {paleta["turquesa_cyan"]};
        color: #FFFFFF !important;
        margin-bottom: 20px;
        box-shadow: 0 8px 20px -5px rgba(11, 25, 44, 0.4);
    }}

    .main-header-enterprise h1 {{
        font-weight: 800 !important;
        color: #FFFFFF !important;
        margin: 0;
        font-size: 22px;
        letter-spacing: 0.5px;
    }}

    .main-header-enterprise p {{
        color: {paleta["turquesa_cyan"]} !important;
        margin: 4px 0 0 0;
        font-size: 13px;
        font-weight: 700;
    }}

    .kpi-wrapper-grid {{
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 14px;
        margin-bottom: 20px;
    }}

    .kpi-card-enterprise {{
        background: #FFFFFF !important;
        border: 2px solid {paleta["azul_marina"]} !important;
        border-radius: 12px;
        padding: 16px 14px;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.06);
        position: relative;
    }}

    .kpi-card-enterprise::before {{
        content: "";
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 5px;
        background: {paleta["turquesa_cyan"]};
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
    }}

    .kpi-card-title {{
        font-weight: 800 !important;
        color: {paleta["azul_marina"]} !important;
        font-size: 11px !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 6px;
    }}

    .kpi-card-value {{
        font-weight: 900 !important;
        color: #000000 !important;
        font-size: 26px !important;
        line-height: 1.1;
    }}

    .kpi-card-subtitle {{
        font-size: 10px !important;
        color: #475569 !important;
        margin-top: 4px;
        font-weight: 700 !important;
    }}

    /* CONTENEDOR GENERAL DE PESTAÑAS */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 6px;
        background-color: {paleta["azul_marina"]} !important;
        padding: 6px;
        border-radius: 10px;
    }}

    /* BOTONES DE PESTAÑA INACTIVOS */
    .stTabs [data-baseweb="tab"] {{
        height: 42px;
        background-color: transparent !important;
        border-radius: 6px;
        color: #FFFFFF !important;
        font-weight: 700;
        font-size: 13px;
        border: none !important;
    }}

    /* BOTÓN DE PESTAÑA SELECCIONADO (ACTIVO - CORREGIDO COLOR TURQUESA) */
    .stTabs [aria-selected="true"] {{
        background-color: {paleta["azul_noche"]} !important;
        color: #FFFFFF !important;
        border-bottom: 3px solid {paleta["turquesa_cyan"]} !important;
        font-weight: 800 !important;
    }}

    /* SUB-TABS INTERNOS */
    div[data-baseweb="tab-list"] button[aria-selected="true"] {{
        background-color: {paleta["azul_noche"]} !important;
        color: #FFFFFF !important;
    }}

    /* TABLAS DATAFRAME */
    div[data-testid="stDataFrame"], div[aria-label="st.dataframe"] {{
        background-color: #FFFFFF !important;
        border: 2px solid {paleta["azul_marina"]} !important;
        border-radius: 12px !important;
        padding: 4px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05) !important;
    }}

    div[data-testid="stDataFrame"] iframe {{
        background-color: #FFFFFF !important;
    }}

    .matrix-title-card {{
        background-color: #FFFFFF;
        border-left: 5px solid {paleta["naranja_desierto"]};
        border-top: 1px solid #E2E8F0;
        border-right: 1px solid #E2E8F0;
        border-bottom: 1px solid #E2E8F0;
        padding: 12px 16px;
        border-radius: 6px;
        margin-top: 15px;
        margin-bottom: 12px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.03);
    }}

    /* BOTONES SECUNDARIOS Y UPLOADERS */
    div.stButton > button[kind="secondary"], 
    div.stButton > button:not([kind="primary"]),
    div.stDownloadButton > button,
    [data-testid="stFileUploader"] section button,
    [data-testid="stFileUploader"] label button {{
        background-color: #FFFFFF !important;
        color: #1E293B !important;
        border: 1px solid #CBD5E1 !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        box-shadow: 0px 2px 4px rgba(0,0,0,0.05) !important;
    }}

    div.stButton > button[kind="secondary"]:hover, 
    div.stButton > button:not([kind="primary"]):hover,
    div.stDownloadButton > button:hover,
    [data-testid="stFileUploader"] section button:hover,
    [data-testid="stFileUploader"] label button:hover {{
        background-color: #F8FAFC !important;
        border-color: #94A3B8 !important;
        color: #0F172A !important;
    }}
    </style>
    """
    st.markdown(css_custom, unsafe_allow_html=True)
    
# ==============================================================================
# 8. NAVEGACIÓN PRINCIPAL
# ==============================================================================

def main() -> None:
    seccion = st.sidebar.radio(
        "Menú principal",
        ["📊 Consultar dashboard", "📝 Capturar / actualizar datos"],
        index=1, key="seccion_principal",
    )

    inyectar_estilos_css_enterprise()
    inyectar_estilos_base_ui()

    # --------------------------------------------------------------------------
    # ESTILO FORZADO PARA PESTAÑAS (TABS) - VISIBILIDAD TOTAL EN CUALQUIER TEMA
    # --------------------------------------------------------------------------
    

    # ENCABEZADO PRINCIPAL CON BOTONES DE ACTUALIZACIÓN DERECHA
    col_hdr_left, col_hdr_right = st.columns([0.70, 0.30])
    
    with col_hdr_left:
        st.markdown(f"""
            <div class="main-header-enterprise">
                <h1>OPERACIONES — REGIÓN NORTE LA BAJA</h1>
                <p>Módulo Consolidado de Analítica, Pólizas y Control Técnico de Campo ({ANIO_BASE_ESTRICTO})</p>
            </div>
        """, unsafe_allow_html=True)

    with col_hdr_right:
        st.write("")
        st.markdown("""
            <style>
            /* Botón Primario (Recargar) */
            div.stButton > button[kind="primary"] {
                background-color: #1E293B !important;
                color: #FFFFFF !important;
                border: 1px solid #1E293B !important;
                border-radius: 8px !important;
                font-weight: 600 !important;
                box-shadow: 0px 2px 4px rgba(0,0,0,0.1) !important;
            }
            div.stButton > button[kind="primary"]:hover {
                background-color: #0F172A !important;
                border-color: #0F172A !important;
            }

            /* Botón Secundario (Limpiar Caché) */
            div.stButton > button[kind="secondary"], div.stButton > button:not([kind="primary"]) {
                background-color: #FFFFFF !important;
                color: #1E293B !important;
                border: 1px solid #CBD5E1 !important;
                border-radius: 8px !important;
                font-weight: 600 !important;
                box-shadow: 0px 2px 4px rgba(0,0,0,0.05) !important;
            }
            div.stButton > button[kind="secondary"]:hover, div.stButton > button:not([kind="primary"]):hover {
                background-color: #F8FAFC !important;
                border-color: #94A3B8 !important;
                color: #0F172A !important;
            }
            </style>
        """, unsafe_allow_html=True)

        btn_c1, btn_c2 = st.columns(2)
        with btn_c1:
            if st.button("🔄 Recargar", use_container_width=True, type="primary"):
                st.cache_data.clear()
                st.rerun()
        with btn_c2:
            if st.button("🧹 Limpiar Caché", use_container_width=True, type="secondary"):
                st.cache_data.clear()
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

    if seccion == "📝 Capturar / actualizar datos":
        renderizar_modulo_carga_github()
        return

    df_raw = ejecutar_pipeline_ingestion_datos()

    if df_raw.empty:
        st.error("⚠️ No hay datos disponibles en el repositorio de GitHub. Verifica el token, el repositorio y que existan archivos en la carpeta configurada.")
        st.info("Abre '📝 Capturar / actualizar datos' en el menú lateral para pegar los registros e iniciar la carga.")
        return

    # --------------------------------------------------------------------------
    # BARRA LATERAL (SIDEBAR DE FILTROS A LA IZQUIERDA)
    # --------------------------------------------------------------------------
    st.sidebar.markdown("### 📅 Dimensión Temporal")
    dimension_sel = st.sidebar.radio(
        "Agrupar tiempo por:",
        ["SEMANA_DIM", "FECHA_TRUNCADA", "MES_DIM", "AÑO_DIM"],
        index=0,
        format_func=lambda x: {
            "SEMANA_DIM": "Semana",
            "FECHA_TRUNCADA": "Día",
            "MES_DIM": "Mes",
            "AÑO_DIM": "Año"
        }[x]
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔍 Filtos Dinámicos")

    df_temp = df_raw.copy()

    meses_disponibles = [m for m in LISTA_ORDENADA_MESES if m in df_temp["MES_DIM"].unique()]
    filtro_meses_sel = st.sidebar.multiselect("Mes:", meses_disponibles)
    if filtro_meses_sel:
        df_temp = df_temp[df_temp["MES_DIM"].isin(filtro_meses_sel)]

    semanas_disponibles = sorted([str(s) for s in df_temp["SEMANA_DIM"].dropna().unique()])
    filtro_semanas_sel = st.sidebar.multiselect("Semana:", semanas_disponibles)
    if filtro_semanas_sel:
        df_temp = df_temp[df_temp["SEMANA_DIM"].isin(filtro_semanas_sel)]

    polizas_existentes = sorted([k for k in df_temp["Codigo_Poliza"].unique() if k in MAPEO_POLIZAS])
    opciones_poliza = [f"{cod} - {MAPEO_POLIZAS[cod]}" for cod in polizas_existentes]
    filtro_polizas_sel = st.sidebar.multiselect("Pólizas:", opciones_poliza)
    codigos_poliza_sel = [p.split(" - ")[0] for p in filtro_polizas_sel]
    if codigos_poliza_sel:
        df_temp = df_temp[df_temp["Codigo_Poliza"].isin(codigos_poliza_sel)]

    tipos_eventos = sorted(list(df_temp["Tipo_Orden"].unique()))
    filtro_eventos_sel = st.sidebar.multiselect("Tipo de Evento / Orden:", tipos_eventos)
    if filtro_eventos_sel:
        df_temp = df_temp[df_temp["Tipo_Orden"].isin(filtro_eventos_sel)]

    distritos = sorted(list(df_temp["Distrito"].unique()))
    filtro_distritos = st.sidebar.multiselect("Distrito / Zona:", distritos)
    if filtro_distritos:
        df_temp = df_temp[df_temp["Distrito"].isin(filtro_distritos)]

    empresas = sorted(list(df_temp["Empresa"].unique()))
    filtro_empresas = st.sidebar.multiselect("Proveedor / Empresa:", empresas)
    if filtro_empresas:
        df_temp = df_temp[df_temp["Empresa"].isin(filtro_empresas)]

    df_filtrado = df_temp

    if not codigos_poliza_sel:
        df_filtrado = df_filtrado[df_filtrado["Codigo_Poliza"].isin(MAPEO_POLIZAS.keys())]

    df_folios = df_filtrado.drop_duplicates(subset=["FOLIO_KEY"], keep="first")

    # --------------------------------------------------------------------------
    # PESTAÑAS PRINCIPALES
    # --------------------------------------------------------------------------

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Pólizas & Cuadrillas",
        "🔄 Reincidencias Total",
        "🛠️ Cambios de equipo",
        "🎧 Causa & Solución soporte"
    ])

    with tab1:
        renderizar_pestana_polizas_cuadrillas(df_folios, dimension_sel)

    with tab2:
        renderizar_pestana_reincidencias_total(df_folios, dimension_sel)

    with tab3:
        st.markdown("### 🛠️ Cambios de equipo")
        st.info("ℹ️ PROXIMAMENTE.")

    with tab4:
        st.markdown("### 🎧 Causa & Solución soporte")
        st.info("ℹ️ PROXIMAMENTE.")


if __name__ == "__main__":
    main()
