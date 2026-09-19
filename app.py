# ==============================================================================
# SISTEMA ENTERPRISE DE CONTROL OPERATIVO DE CUADRILLAS EN CAMPO 2026
# Archivo: app.py | Versión: 15.0.0-MASTER
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
import gc
import calendar

st.set_page_config(
    page_title="Control Operativo Cuadrillas 2026",
    page_icon="[TP]",
    layout="wide",
    initial_sidebar_state="expanded",
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ControlCuadrillas")

# ==============================================================================
# 1. CONFIGURACIÓN GITHUB
# ==============================================================================
try:
    gh_cfg = st.secrets.get("github", {})
except FileNotFoundError:
    gh_cfg = {}

GITHUB_USER   = gh_cfg.get("user",   "jluisnavag-a11y")
GITHUB_REPO   = gh_cfg.get("repo",   "dashboard_jlng_operacoones_norte_cierre_diario")
GITHUB_BRANCH = gh_cfg.get("branch", "main")
GITHUB_FOLDER = gh_cfg.get("folder", "datos_semanales")
GITHUB_TOKEN  = gh_cfg.get("token",  "")

HEADERS          = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
GITHUB_API_URL   = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{GITHUB_FOLDER}?ref={GITHUB_BRANCH}"
GITHUB_RAW_BASE  = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FOLDER}"

# ==============================================================================
# 2. CONSTANTES GLOBALES
# ==============================================================================
ANIO_BASE_ESTRICTO: int       = 2026
EXCEL_EPOCH_START: pd.Timestamp = pd.Timestamp("1899-12-30")
NOMBRE_SISTEMA: str           = "TOTALPLAY / OPERACIONES — REGIÓN NORTE LA BAJA"
VERSION_SISTEMA: str          = "15.0.0-MASTER"

# Tipos elegibles para denominador de efectividad y para el cálculo de productividad
TIPOS_ELEGIBLES_EFECTIVIDAD = frozenset(["INSTALACION", "INSTALACIÓN", "SOPORTE", "CAMBIO DE DOMICILIO", "CAMBIO DE EQUIPO"])

MAPEO_POLIZAS: Dict[str, str] = {
    "R3": "RECOLECCIÓN", "E3": "PÓLIZA 3",
    "M3": "MULTIDISTRITO", "M4": "MULTIDISTRITO FLOTANTE",
    "MT": "MTTO PI", "D1": "DESTAJO"
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
    1:"Enero", 2:"Febrero", 3:"Marzo", 4:"Abril",
    5:"Mayo", 6:"Junio", 7:"Julio", 8:"Agosto",
    9:"Septiembre", 10:"Octubre", 11:"Noviembre", 12:"Diciembre"
}
LISTA_ORDENADA_MESES: List[str] = list(MAPEO_MESES_TEXTO.values())

MAPEO_MESES_NUM: Dict[str, int] = {v: k for k, v in MAPEO_MESES_TEXTO.items()}

LISTA_ALIAS_CREACION: List[str] = ["creacion","creación","created","fecha_creacion","created_at","f_creacion"]
LISTA_ALIAS_TERMINO:  List[str] = ["termino","término","fecha termino","fecha_termino","f_termino","fecha fin","fecha_fin","fecha cierre","closed_at","end_date","finalizacion","finalización"]
LISTA_ALIAS_USUARIO:  List[str] = ["usuario instalador","usuario_instalador","instalador","usuario","usr","id_usuario","tech_id"]
LISTA_ALIAS_NOMBRE:   List[str] = ["nombre tecnico","nombre técnico","nombre_tecnico","tecnico","técnico","nombre","tech_name"]
LISTA_ALIAS_ORDEN:    List[str] = ["orden_servicio","orden servicio","os","orden","folio","service_order"]
LISTA_ALIAS_CUENTA:   List[str] = ["cuenta","account","cta","num_cuenta"]
LISTA_ALIAS_OT:       List[str] = ["ot","orden_trabajo","orden trabajo","work_order","num_ot"]
LISTA_ALIAS_TIPO:     List[str] = ["tipo de orden","tipo_orden","tipo orden","tipo_evento","tipo","evento","order_type"]
LISTA_ALIAS_PROVEEDOR:List[str] = ["empresa","proveedor","contratista","vendor","company"]
LISTA_ALIAS_DISTRITO: List[str] = ["distrito","region","región","zona","sucursal","district"]
LISTA_ALIAS_CLUSTER:  List[str] = ["cluster","clúster","nodo","sector","zona_cluster","ampliacion"]
LISTA_ALIAS_FALLA:    List[str] = ["falla","observaciones","descripcion"]
LISTA_ALIAS_CAUSA:    List[str] = ["causa","motivo","subtipo","diagnostico"]
LISTA_ALIAS_SOLUCION: List[str] = ["solucion","solución","solution","resolucion","resolución"]
LISTA_ALIAS_ESTATUS:  List[str] = ["estatus","status","estado"]

PALETA_COLOR: Dict[str, str] = {
    "azul_noche":      "#0B192C",
    "azul_marina":     "#1E3E62",
    "turquesa_cyan":   "#00D2C8",
    "verde_montana":   "#10B981",
    "naranja_desierto":"#F97316",
    "amarillo_sol":    "#FBBF24",
    "blanco_puro":     "#FFFFFF",
    "gris_borde":      "#CBD5E1",
    "texto_negro":     "#000000"
}

MAPEO_BASE_CLUSTERS: Dict[str, str] = {
    k: k for k in [
        "AGUA CALIENTE","ALTAMIRA","AVIACION","CERRO COLORADO","COLINAS DEL FLORIDO",
        "DOS MIL","FONTANA","FUNDADORES","GONZALEZ ORTEGA","HACIENDA REAL",
        "LOMAS DE VIRREY","MAGISTERIAL","MATAMOROS","NATURA","OTAY","PACIFICO",
        "PATRIA NUEVA","PLAYA HERMOSA","PLAYAS TIJUANA","PUEBLO NUEVO","REFUGIO",
        "ROSAMAR","ROSAS MAGALLON","SALVATIERRA","SANTA FE TIJUANA","UABC",
        "TECATE","VALLE VERDE","VILLA DEL CAMPO","ZAPATA"
    ]
}
RE_SEMANA = re.compile(r"(?:SEM|SEMANA|S)[\s_\-]*(\d{1,2})", re.IGNORECASE)

# ==============================================================================
# 3. DATACLASSES
# ==============================================================================
@dataclass
class MetricasResumenKPI:
    total_eventos:      int
    total_usuarios:     int
    dias_operativos:    int
    productividad_diaria: float
    eventos_r3:         int
    eventos_mt:         int

# ==============================================================================
# 4. FUNCIONES AUXILIARES DE TRANSFORMACIÓN Y LIMPIEZA
# ==============================================================================

def sanitizar_cadena_texto(val: Any) -> str:
    if pd.isna(val) or val is None:
        return "SIN ESPECIFICAR"
    txt = str(val).strip()
    if txt == "" or txt.lower() in ["nan","null","none","<na>"]:
        return "SIN ESPECIFICAR"
    return re.sub(r"\s+", " ", txt).upper()


def sanitizar_folio_identificador(val: Any) -> str:
    if pd.isna(val) or val is None:
        return "SIN_FOLIO"
    txt = re.sub(r"[^A-Z0-9\-_]", "", str(val).strip().upper())
    return txt if txt else "SIN_FOLIO"


def extraer_numero_semana_archivo(nombre_archivo) -> Optional[int]:
    if not isinstance(nombre_archivo, str) or pd.isna(nombre_archivo):
        return None
    match = RE_SEMANA.search(nombre_archivo)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, TypeError):
            pass
    if str(nombre_archivo).strip().isdigit():
        return int(nombre_archivo)
    return None


def _normalizar_cluster_string(txt: str) -> str:
    if txt == "SIN ESPECIFICAR":
        return "CLUSTER GENERAL"
    txt_c = re.sub(r"^AMPLIACI[OÓ]N\s+", "", txt)
    txt_c = re.sub(r"_\d+_[A-Z]$|_[A-Z]$|\s+\d+$", "", txt_c).strip()
    for c_base in MAPEO_BASE_CLUSTERS:
        if c_base in txt_c or txt_c in c_base:
            return c_base
    return txt_c if txt_c else "CLUSTER GENERAL"


def normalizar_clusters_vectorizado(serie: pd.Series) -> pd.Series:
    mapa = {v: _normalizar_cluster_string(str(v)) for v in serie.unique()}
    return serie.map(mapa)


def detectar_columna_por_patrones(columnas: List[str], patrones: List[str]) -> Optional[str]:
    for col in columnas:
        col_lower = str(col).lower().strip()
        for patron in patrones:
            if patron.lower() in col_lower:
                return col
    return None


def parsear_columna_fecha_robusta(serie_raw: pd.Series) -> pd.Series:
    """Convierte seriales Excel (46025.46) y texto a datetime."""
    if serie_raw.empty:
        return pd.Series(pd.NaT, index=serie_raw.index)
    serie_num = pd.to_numeric(serie_raw, errors="coerce")
    mask_num  = serie_num.notna()
    serie_res = pd.Series(pd.NaT, index=serie_raw.index)
    if mask_num.any():
        sub = serie_num[mask_num]
        mask_excel = (sub >= 20000) & (sub <= 80000)
        if mask_excel.any():
            serie_res.loc[sub[mask_excel].index] = pd.to_datetime(
                sub[mask_excel], unit="D", origin=EXCEL_EPOCH_START, errors="coerce"
            )
    mask_txt = ~mask_num
    if mask_txt.any():
        serie_res.loc[mask_txt] = pd.to_datetime(
            serie_raw[mask_txt].astype(str).str.strip(), dayfirst=True, errors="coerce"
        )
    return serie_res


def _dias_calendario_para_periodo(dimension: str, valor_dim: str, df_grupo: pd.DataFrame) -> int:
    """
    Dias con actividad real para el denominador de productividad de UN periodo.
    SEMANA_DIM -> fechas unicas reales (1..7), sin cap artificial.
    Si el archivo fue subido el martes, hay 2 dias y se divide entre 2.
    """
    if dimension == "FECHA_TRUNCADA":
        return 1
    if "FECHA_TRUNCADA" in df_grupo.columns:
        dias_obs = int(df_grupo["FECHA_TRUNCADA"].nunique())
        return max(1, dias_obs)
    if dimension == "AÑO_DIM":
        return 366 if calendar.isleap(ANIO_BASE_ESTRICTO) else 365
    if dimension == "MES_DIM":
        num_mes = MAPEO_MESES_NUM.get(str(valor_dim).strip().capitalize(), 1)
        return calendar.monthrange(ANIO_BASE_ESTRICTO, num_mes)[1]
    return 7


# ==============================================================================
# 5. MOTOR DE REINCIDENCIAS (REGLAS DE NEGOCIO ENTERPRISE)
# ==============================================================================

# Tipos de evento que califican como antecedente válido de una reincidencia
TIPOS_ANTECEDENTE_VALIDO = frozenset([
    "INSTALACION","INSTALACIÓN","SOPORTE",
    "CAMBIO DE DOMICILIO","CAMBIO DE EQUIPO"
])


def _tipo_es_soporte(tipo_str: str) -> bool:
    if pd.isna(tipo_str):
        return False
    return "SOPORTE" in str(tipo_str).upper()


def _tipo_es_antecedente_valido(tipo_str: str) -> bool:
    if pd.isna(tipo_str):
        return False
    t = str(tipo_str).strip().upper()
    return any(elegible in t for elegible in TIPOS_ANTECEDENTE_VALIDO)


def obtener_valor_tipo2(valor_causa, valor_tipo_orden) -> str:
    """Si la causa es N/A o vacía la reemplaza por el valor de Tipo_Orden."""
    val_tipo = str(valor_tipo_orden).strip() if pd.notna(valor_tipo_orden) else "SIN TIPO"
    if val_tipo.upper() in ["NONE","NULL","NA","N/A","NAN",""]:
        val_tipo = "SIN TIPO"
    if pd.isna(valor_causa):
        return val_tipo
    val_causa_str = str(valor_causa).strip()
    if val_causa_str.upper() in ["NONE","NULL","NA","N/A","NAN",""]:
        return val_tipo
    return val_causa_str


@st.cache_data(ttl=3600, show_spinner=False, max_entries=2)
def calcular_reincidencias_vectorizadas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Motor de reincidencias enterprise.

    Reglas:
    1. Ordenamiento exclusivo por Num_Semana (asc) y posición ordinal de fila.
       No se usa fecha de cierre para ordenar.
    2. Una visita es reincidencia si:
       a) Tipo_Orden actual es SOPORTE.
       b) El antecedente inmediato (visita N-1) pertenece a: Instalación, Soporte,
          Cambio de domicilio o Cambio de equipo.
       c) La brecha entre fechas de creación es <= 60 días.
    3. Si la causa del antecedente es N/A, se usa el valor de Tipo_Orden como causa.
    4. El ID primario del técnico es Usuario; el nombre es complementario.
    5. La búsqueda de antecedentes evalúa el DATASET COMPLETO (sin filtros de UI).
    """
    # Inicialización de columnas de salida
    df = df.copy()
    for col, val in [
        ("ES_REINCIDENCIA", "NO"),
        ("CONTEO_PREVIO_8_SEM", 0),
        ("Usuario_Origen_Reincidencia", "N/A"),
        ("Empresa_Origen_Reincidencia", "N/A"),
        ("Semana_Origen_Reincidencia", np.nan),
        ("Causa_Origen", "N/A"),
        ("TIPO_2", "N/A"),
        ("Falla_Nueva", "N/A"),
        ("ES_CASO_ESPECIAL", "NO"),
    ]:
        df[col] = val

    if df.empty:
        return df

    cols = list(df.columns)
    col_tech    = detectar_columna_por_patrones(cols, ["usuario_tecnico","tecnico","tech","usuario","atendio","nombre_tecnico"]) or "Usuario_Tecnico"
    col_semana  = detectar_columna_por_patrones(cols, ["num_semana_archivo","semana","sem"]) or "Num_Semana_Archivo"
    col_causa   = detectar_columna_por_patrones(cols, LISTA_ALIAS_CAUSA) or "Tipo_Orden"
    col_falla   = detectar_columna_por_patrones(cols, LISTA_ALIAS_FALLA) or "Tipo_Orden"
    col_empresa = detectar_columna_por_patrones(cols, LISTA_ALIAS_PROVEEDOR) or "Empresa"
    col_tipo    = "Tipo_Orden" if "Tipo_Orden" in cols else col_causa

    df["_IDX_ORIG"] = range(len(df))
    df["_SEM_TEMP"] = pd.to_numeric(df[col_semana], errors="coerce").fillna(0).astype(int)

    # Fechas para cálculo de brecha 60 días
    col_dt_parsed = "_datetime_parsed"
    tiene_fechas  = col_dt_parsed in df.columns

    mask_cta = df["Cuenta_Cliente"].notna() & (
        ~df["Cuenta_Cliente"].astype(str).str.upper().isin(["SIN_CTA","SIN_FOLIO","NAN","NONE",""])
    )
    df_valid = df[mask_cta].sort_values(["Cuenta_Cliente","_SEM_TEMP","_IDX_ORIG"]).copy()

    dict_rein: Dict[str, dict] = {}

    for cuenta, g in df_valid.groupby("Cuenta_Cliente"):
        registros = g.to_dict("records")
        n = len(registros)
        if n < 2:
            continue

        for i in range(1, n):
            reg_act  = registros[i]
            tipo_act = reg_act.get("Tipo_Orden", reg_act.get("TIPO", ""))

            if not _tipo_es_soporte(tipo_act):
                continue

            # Buscar antecedente válido más reciente hacia atrás
            reg_prev = None
            for j in range(i - 1, -1, -1):
                tipo_prev_candidato = registros[j].get("Tipo_Orden", registros[j].get("TIPO", ""))
                if _tipo_es_antecedente_valido(tipo_prev_candidato):
                    reg_prev = registros[j]
                    break

            if reg_prev is None:
                continue

            # Validar brecha <= 60 días si hay fechas disponibles
            if tiene_fechas:
                fecha_act  = reg_act.get(col_dt_parsed)
                fecha_prev = reg_prev.get(col_dt_parsed)
                if pd.notna(fecha_act) and pd.notna(fecha_prev):
                    delta = (pd.Timestamp(fecha_act) - pd.Timestamp(fecha_prev)).days
                    if delta > 60 or delta < 0:
                        continue
                    semana_origen = reg_prev.get("_SEM_TEMP", np.nan)
                else:
                    # Sin fechas, se valida solo por semana (brecha <= 8 semanas)
                    semana_origen = reg_prev.get("_SEM_TEMP", np.nan)
                    if pd.notna(semana_origen):
                        brecha_sem = int(reg_act.get("_SEM_TEMP", 0)) - int(semana_origen)
                        if brecha_sem > 8 or brecha_sem < 0:
                            continue
            else:
                semana_origen = reg_prev.get("_SEM_TEMP", np.nan)

            tech_prev = str(reg_prev.get(col_tech, "SIN ESPECIFICAR"))
            if tech_prev.upper() in ["NAN","NONE","","N/A","NULL"]:
                tech_prev = "SIN ESPECIFICAR"

            emp_prev = str(reg_prev.get(col_empresa, "SIN EMPRESA"))
            if emp_prev.upper() in ["NAN","NONE","","N/A","NULL"]:
                emp_prev = "SIN EMPRESA"

            causa_raw = reg_prev.get(col_causa)
            tipo_raw  = reg_prev.get(col_tipo)
            tipo_2    = obtener_valor_tipo2(causa_raw, tipo_raw)
            falla_val = reg_act.get(col_falla, "N/A")

            key_act = reg_act.get("FOLIO_KEY")
            if key_act:
                dict_rein[key_act] = {
                    "Usuario_Origen":  tech_prev,
                    "Empresa_Origen":  emp_prev,
                    "Semana_Origen":   semana_origen,
                    "Causa_Origen":    str(causa_raw) if pd.notna(causa_raw) else "N/A",
                    "TIPO_2":          tipo_2,
                    "Falla_Nueva":     falla_val,
                }

    if dict_rein:
        mask_r = df["FOLIO_KEY"].isin(dict_rein)
        df.loc[mask_r, "ES_REINCIDENCIA"]           = "SI"
        df.loc[mask_r, "CONTEO_PREVIO_8_SEM"]       = 1
        _map = lambda campo: df.loc[mask_r, "FOLIO_KEY"].map(
            lambda k: dict_rein[k][campo] if k in dict_rein else "N/A"
        )
        df.loc[mask_r, "Usuario_Origen_Reincidencia"]  = _map("Usuario_Origen")
        df.loc[mask_r, "Empresa_Origen_Reincidencia"]  = _map("Empresa_Origen")
        df.loc[mask_r, "Semana_Origen_Reincidencia"]   = df.loc[mask_r, "FOLIO_KEY"].map(
            lambda k: dict_rein[k]["Semana_Origen"] if k in dict_rein else np.nan
        )
        df.loc[mask_r, "Causa_Origen"]                 = _map("Causa_Origen")
        df.loc[mask_r, "TIPO_2"]                       = _map("TIPO_2")
        df.loc[mask_r, "Falla_Nueva"]                  = _map("Falla_Nueva")

    df.drop(columns=["_IDX_ORIG","_SEM_TEMP"], errors="ignore", inplace=True)
    return df


# ==============================================================================
# 6. PIPELINE DE INGESTIÓN
# ==============================================================================

def descargar_y_procesar_archivo(nombre_archivo: str) -> Optional[pd.DataFrame]:
    try:
        resp = requests.get(f"{GITHUB_RAW_BASE}/{nombre_archivo}", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            bdata = io.BytesIO(resp.content)
            if nombre_archivo.endswith(".parquet"):
                df_temp = pd.read_parquet(bdata)
            else:
                try:
                    df_temp = pd.read_csv(bdata, low_memory=False, dtype=str, encoding="utf-8", on_bad_lines="skip")
                except Exception:
                    bdata.seek(0)
                    df_temp = pd.read_csv(bdata, low_memory=False, dtype=str, encoding="latin1", on_bad_lines="skip")
            if df_temp is not None and not df_temp.empty:
                df_temp["Archivo_Origen"] = str(nombre_archivo)
                return df_temp
    except Exception as e:
        logger.error(f"Error procesando {nombre_archivo}: {e}")
    return None


def transformar_dataset_completo(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica todas las transformaciones de negocio sobre un DataFrame crudo."""
    if df is None or df.empty:
        return df

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cols = list(df.columns)

    get_col   = lambda alias: detectar_columna_por_patrones(cols, alias)
    sanit_fol = lambda s: s.apply(sanitizar_folio_identificador)
    sanit_txt = lambda s: s.apply(sanitizar_cadena_texto)

    # Fechas
    col_fecha     = get_col(LISTA_ALIAS_CREACION)
    col_fecha_fin = get_col(LISTA_ALIAS_TERMINO)
    df["_datetime_parsed"]  = parsear_columna_fecha_robusta(df[col_fecha])  if col_fecha  and col_fecha  in cols else pd.NaT
    df["_datetime_termino"] = parsear_columna_fecha_robusta(df[col_fecha_fin]) if col_fecha_fin and col_fecha_fin in cols else pd.NaT
    delta_h = (df["_datetime_termino"] - df["_datetime_parsed"]).dt.total_seconds() / 3600.0
    df["Tiempo_Resolucion_Horas"] = delta_h.where(delta_h >= 0)

    # Columnas clave
    c_os  = get_col(LISTA_ALIAS_ORDEN);    c_cta = get_col(LISTA_ALIAS_CUENTA)
    c_ot  = get_col(LISTA_ALIAS_OT);      c_tipo = get_col(LISTA_ALIAS_TIPO)
    c_usr = get_col(LISTA_ALIAS_USUARIO); c_nom  = get_col(LISTA_ALIAS_NOMBRE)
    c_prov= get_col(LISTA_ALIAS_PROVEEDOR);c_dist= get_col(LISTA_ALIAS_DISTRITO)
    c_clus= get_col(LISTA_ALIAS_CLUSTER)

    # Columnas opcionales (causa, falla, solución, estatus)
    c_causa  = get_col(LISTA_ALIAS_CAUSA)
    c_falla  = get_col(LISTA_ALIAS_FALLA)
    c_sol    = get_col(LISTA_ALIAS_SOLUCION)
    c_status = get_col(LISTA_ALIAS_ESTATUS)
    if c_causa  and c_causa  in cols: df["Causa_Registro"]  = sanit_txt(df[c_causa])
    if c_falla  and c_falla  in cols: df["Falla_Registro"]  = sanit_txt(df[c_falla])
    if c_sol    and c_sol    in cols: df["Solucion_Registro"]= sanit_txt(df[c_sol])
    if c_status and c_status in cols: df["Estatus_Registro"] = sanit_txt(df[c_status])

    s_os   = sanit_fol(df[c_os])   if c_os   else pd.Series("SIN_OS",  index=df.index)
    s_cta  = sanit_fol(df[c_cta])  if c_cta  else pd.Series("SIN_CTA", index=df.index)
    s_ot   = sanit_fol(df[c_ot])   if c_ot   else pd.Series("SIN_OT",  index=df.index)
    s_tipo = sanit_txt(df[c_tipo])  if c_tipo else pd.Series("EVENTO GENERAL", index=df.index)

    df["FOLIO_KEY"]      = s_os.astype(str) + "_" + s_cta.astype(str) + "_" + s_ot.astype(str) + "_" + s_tipo.astype(str)
    df["Cuenta_Cliente"] = s_cta.astype(str)

    s_u = sanit_txt(df[c_usr]) if c_usr else pd.Series("SIN ESPECIFICAR", index=df.index)
    s_n = sanit_txt(df[c_nom]) if c_nom else pd.Series("SIN ESPECIFICAR", index=df.index)
    df["Usuario_Tecnico"] = np.where(
        (s_u != "SIN ESPECIFICAR") & (s_n != "SIN ESPECIFICAR"), s_u + " | " + s_n,
        np.where(s_n != "SIN ESPECIFICAR", s_n, s_u)
    )

    df["Empresa"]      = sanit_txt(df[c_prov]) if c_prov else "SIN PROVEEDOR"
    df["Distrito"]     = sanit_txt(df[c_dist]) if c_dist else "DISTRITO GENERAL"
    df["Tipo_Orden"]   = s_tipo
    df["Cluster_Raw"]  = sanit_txt(df[c_clus]) if c_clus else "SIN CLUSTER"
    df["Cluster_Base"] = normalizar_clusters_vectorizado(df["Cluster_Raw"])

    # Pólizas: el ID primario es el campo Usuario (primeros 5 chars → posiciones 3-4)
    c_pol = c_usr or c_nom
    if c_pol and c_pol in cols:
        sub_cods = df[c_pol].astype(str).str[3:5]
        df["Codigo_Poliza"] = np.where(sub_cods.isin(MAPEO_POLIZAS), sub_cods, "")
        df["Nombre_Poliza"] = df["Codigo_Poliza"].map(MAPEO_POLIZAS).fillna("NO VALIDO")
    else:
        df["Codigo_Poliza"] = ""; df["Nombre_Poliza"] = "NO VALIDO"

    # Dimensiones temporales
    sem_arch = df["Archivo_Origen"].apply(extraer_numero_semana_archivo) if "Archivo_Origen" in cols else pd.Series(None, index=df.index)
    sem_iso  = pd.to_numeric(df["_datetime_parsed"].dt.isocalendar().week, errors="coerce").fillna(0).astype(int)
    df["Num_Semana_Archivo"] = np.where(
        pd.to_numeric(sem_arch, errors="coerce").fillna(0) > 0,
        pd.to_numeric(sem_arch, errors="coerce").fillna(0).astype(int),
        sem_iso
    )

    df["AÑO_DIM"]   = str(ANIO_BASE_ESTRICTO)
    df["SEMANA_DIM"] = df["Num_Semana_Archivo"].apply(
        lambda x: f"Sem {int(x)}" if x > 0 else "SIN_FECHA"
    )
    df["MES_DIM"]    = df["_datetime_parsed"].dt.month.fillna(1).astype(int).map(MAPEO_MESES_TEXTO).fillna("Enero")

    dates_valid = df["_datetime_parsed"].dropna()
    df["FECHA_TRUNCADA"] = f"01.01.{ANIO_BASE_ESTRICTO}"
    if not dates_valid.empty:
        df.loc[dates_valid.index, "FECHA_TRUNCADA"] = dates_valid.dt.strftime(f"%d.%m.{ANIO_BASE_ESTRICTO}")

    # Motor de reincidencias sobre el dataset COMPLETO (sin filtros)
    df = calcular_reincidencias_vectorizadas(df)
    return df


@st.cache_data(ttl=86400, show_spinner="Cargando dataset...", max_entries=1)
def ejecutar_pipeline_ingestion_datos() -> pd.DataFrame:
    """
    Fuente única: GitHub.
    1) Ruta rápida: datos_consolidados.parquet (2-5 seg).
    2) Fallback: descarga en paralelo todos los CSV/parquet de la carpeta.
    """
    cols_base = ["FOLIO_KEY","Cuenta_Cliente","Usuario_Tecnico","Empresa",
                 "Distrito","Tipo_Orden","Cluster_Base","Nombre_Poliza",
                 "Num_Semana_Archivo","SEMANA_DIM","FECHA_TRUNCADA"]

    # Ruta rápida
    try:
        resp = requests.get(f"{GITHUB_RAW_BASE}/datos_consolidados.parquet", headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            df = pd.read_parquet(io.BytesIO(resp.content))
            if not df.empty and "MES_DIM" in df.columns:
                return df
    except Exception as e:
        logger.warning(f"No se pudo descargar Parquet consolidado: {e}")

    # Fallback
    try:
        resp = requests.get(GITHUB_API_URL, headers=HEADERS, timeout=10)
        lista = [
            f["name"] for f in resp.json()
            if isinstance(f, dict) and f["name"].endswith((".csv",".parquet"))
            and f["name"] != "datos_consolidados.parquet"
        ] if resp.status_code == 200 else []
    except Exception as e:
        logger.error(f"Error conectando GitHub API: {e}")
        lista = []

    if not lista:
        return pd.DataFrame(columns=cols_base)

    with ThreadPoolExecutor(max_workers=25) as ex:
        resultados = list(ex.map(descargar_y_procesar_archivo, lista))
    coleccion = [d for d in resultados if d is not None and not d.empty]
    if not coleccion:
        return pd.DataFrame(columns=cols_base)

    df = pd.concat(coleccion, ignore_index=True)
    coleccion.clear(); gc.collect()
    return transformar_dataset_completo(df)


# ==============================================================================
# 7. MÉTRICAS Y GRÁFICOS
# ==============================================================================

def calcular_indice_productividad_diaria(
    total_eventos: int, total_usuarios: int, dias_operativos: int
) -> float:
    """
    Productividad = (Eventos / Usuarios) / Días.
    Los días se calculan según la dimensión temporal activa:
      - Por día   → 1
      - Por semana→ días únicos reales observados en el grupo (1..7)
      - Por mes   → días del mes calendario (28-31)
      - Por año   → 365 / 366
    El llamador debe pasar el valor correcto de dias_operativos.
    No se aplica factor de corrección artificial.
    """
    if total_usuarios <= 0 or dias_operativos <= 0:
        return 0.0
    return round((total_eventos / total_usuarios) / dias_operativos, 2)


def calcular_tendencia_lineal_robusta(valores: List[float]) -> List[float]:
    n = len(valores)
    if n <= 1:
        return [float(v) for v in valores]
    x = np.arange(n); y = np.array(valores, dtype=float)
    z = np.polyfit(x, y, 1); p = np.poly1d(z)
    return [round(float(v), 2) for v in p(x)]


def extraer_metricas_kpi_totales(
    df_folios: pd.DataFrame,
    df_raw_completo: pd.DataFrame,
    dimension: str = "SEMANA_DIM"
) -> MetricasResumenKPI:
    """
    KPI de productividad: SIEMPRE sobre el dataset completo sin filtros de UI.

    Fórmula:
        Productividad = (Eventos totales / Técnicos únicos) / Días con actividad

    Donde:
    - Eventos totales  = len(df_raw_completo) — todos los tipos, pólizas, distritos, empresas.
    - Técnicos únicos  = df_raw_completo["Usuario_Tecnico"].nunique() — ídem.
    - Días con actividad = fechas únicas reales en df_raw_completo["FECHA_TRUNCADA"]
      acotadas según dimensión:
        • SEMANA_DIM   → fechas únicas reales en el período (1..N·7 semanas), sin cap artificial.
        • MES_DIM      → fechas únicas reales del mes (1..31).
        • AÑO_DIM      → fechas únicas reales del año (1..366).
        • FECHA_TRUNCADA → 1.

    Los KPI de conteo (total_eventos) sí reflejan el subconjunto filtrado de df_folios
    para que el usuario pueda ver el impacto de sus filtros.
    """
    total_eventos  = len(df_folios)
    total_usuarios = df_folios["Usuario_Tecnico"].nunique() if total_eventos > 0 else 0

    # Productividad: usa el universo completo (sin filtros de UI)
    ev_prod  = len(df_raw_completo)
    usr_prod = df_raw_completo["Usuario_Tecnico"].nunique() if ev_prod > 0 else 0

    if ev_prod > 0 and "FECHA_TRUNCADA" in df_raw_completo.columns:
        if dimension == "FECHA_TRUNCADA":
            dias_op = 1
        else:
            # Días únicos reales con actividad en todo el dataset — sin cap artificial
            dias_op = max(1, int(df_raw_completo["FECHA_TRUNCADA"].nunique()))
    else:
        dias_op = 1

    prod_diaria = calcular_indice_productividad_diaria(ev_prod, usr_prod, dias_op)
    conteo_r3   = (df_folios["Codigo_Poliza"] == "R3").sum() if "Codigo_Poliza" in df_folios.columns else 0
    conteo_mt   = (df_folios["Codigo_Poliza"] == "MT").sum() if "Codigo_Poliza" in df_folios.columns else 0

    return MetricasResumenKPI(
        total_eventos=total_eventos, total_usuarios=total_usuarios,
        dias_operativos=dias_op, productividad_diaria=prod_diaria,
        eventos_r3=int(conteo_r3), eventos_mt=int(conteo_mt)
    )


def _num_sem(val) -> int:
    try:
        return int(float(str(val).replace("Sem","").replace("Semana","").strip()))
    except (ValueError, TypeError):
        return 999


def generar_figura_evolucion_temporal(df_folios: pd.DataFrame, dimension_temporal: str) -> go.Figure:
    """
    Gráfico de barras (Eventos) + línea (Productividad) con tendencias.
    La productividad usa días reales del período, no días con actividad.
    Productividad incluye TODOS los tipos de evento.
    Eje X: etiquetas cortas (Sem 1, Sem 2…) con ángulo -45° y margen inferior ampliado.
    """
    if df_folios.empty:
        return go.Figure().update_layout(title="Sin datos para la selección actual")

    df_t = df_folios.copy()

    if dimension_temporal == "SEMANA_DIM":
        df_t["SEMANA_DIM"] = df_t["SEMANA_DIM"].apply(
            lambda s: f"Sem {_num_sem(s)}" if _num_sem(s) != 999 else str(s)
        )
        raw = [x for x in df_t["SEMANA_DIM"].dropna().unique() if pd.notna(x)]
        eje_x_base = sorted(raw, key=_num_sem)
    elif dimension_temporal == "MES_DIM":
        eje_x_base = [m for m in LISTA_ORDENADA_MESES if m in df_t["MES_DIM"].unique()]
    elif dimension_temporal == "FECHA_TRUNCADA":
        eje_x_base = sorted(df_t["FECHA_TRUNCADA"].unique())
    else:
        eje_x_base = [str(ANIO_BASE_ESTRICTO)]

    # Agrupaciones
    mapa_ev    = df_t.groupby(dimension_temporal).size().to_dict()
    mapa_usr   = df_t.groupby(dimension_temporal)["Usuario_Tecnico"].nunique().to_dict()
    mapa_grp   = dict(tuple(df_t.groupby(dimension_temporal)))

    eje_x, vals_ev, vals_prod = [], [], []

    for cat in eje_x_base:
        ev = mapa_ev.get(cat, 0)
        if ev <= 0:
            continue
        eje_x.append(str(cat))
        vals_ev.append(float(ev))
        u     = max(mapa_usr.get(cat, 1), 1)
        grupo = mapa_grp.get(cat, pd.DataFrame())
        dias  = _dias_calendario_para_periodo(dimension_temporal, cat, grupo)
        vals_prod.append(calcular_indice_productividad_diaria(ev, u, dias))

    if not eje_x:
        return go.Figure().update_layout(title="Sin registros activos en el rango seleccionado")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=eje_x, y=vals_ev, name="Eventos Completados",
        marker_color=PALETA_COLOR["azul_marina"], opacity=0.9, yaxis="y"
    ))
    fig.add_trace(go.Scatter(
        x=eje_x, y=calcular_tendencia_lineal_robusta(vals_ev),
        name="Tendencia Eventos", mode="lines",
        line=dict(color=PALETA_COLOR["naranja_desierto"], width=2, dash="dash"), yaxis="y"
    ))
    fig.add_trace(go.Scatter(
        x=eje_x, y=vals_prod, name="Productividad / Día", mode="lines+markers",
        line=dict(color=PALETA_COLOR["turquesa_cyan"], width=2),
        marker=dict(size=6, color=PALETA_COLOR["azul_noche"]), yaxis="y2"
    ))
    fig.add_trace(go.Scatter(
        x=eje_x, y=calcular_tendencia_lineal_robusta(vals_prod),
        name="Tendencia Productividad", mode="lines",
        line=dict(color=PALETA_COLOR["verde_montana"], width=1.5, dash="dot"), yaxis="y2"
    ))

    fig.update_layout(
        title={
            "text": f"<b>EVOLUCIÓN TEMPORAL Y TENDENCIAS AJUSTADAS ({dimension_temporal})</b>",
            "y": 0.96, "x": 0.01,
            "font": {"size": 13, "color": "#000000", "family": "Arial, sans-serif"}
        },
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        font={"family": "Arial, sans-serif", "size": 11, "color": "#000000"},
        # Margen inferior ampliado para que las etiquetas del eje X no queden cortadas
        margin=dict(l=50, r=60, t=50, b=130),
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="top", y=-0.38, xanchor="center", x=0.5,
            font=dict(size=10, color="#000000")
        ),
        xaxis=dict(
            showgrid=False, linecolor=PALETA_COLOR["azul_marina"],
            tickfont=dict(color="#000000", size=10),
            tickangle=-45,
            type="category", categoryorder="array", categoryarray=eje_x_base,
        ),
        yaxis=dict(
            title=dict(text="OT / Eventos Completados", font=dict(color="#000000", size=11)),
            showgrid=True, gridcolor="#E2E8F0", linecolor=PALETA_COLOR["azul_marina"],
            tickfont=dict(color="#000000", size=10)
        ),
        yaxis2=dict(
            title=dict(text="Productividad (Eventos / Técnico / Día)", font=dict(color="#000000", size=11)),
            overlaying="y", side="right", showgrid=False, linecolor=PALETA_COLOR["azul_marina"],
            tickfont=dict(color="#000000", size=10),
            range=[0, max(vals_prod + [1.0]) * 1.35]
        ),
        # Deshabilitar zoom táctil (pinch) en móviles
        dragmode=False,
    )
    return fig


# ==============================================================================
# 8. GITHUB HELPERS (CARGA)
# ==============================================================================

def _headers_gh() -> Dict[str, str]:
    h = dict(HEADERS)
    h["Accept"] = "application/vnd.github.v3+json"
    return h


@st.cache_data(ttl=180, show_spinner=False)
def listar_archivos_semanales_github() -> List[str]:
    try:
        resp = requests.get(GITHUB_API_URL, headers=_headers_gh(), timeout=15)
        if resp.status_code == 200:
            return sorted([
                f["name"] for f in resp.json()
                if isinstance(f, dict) and f["name"].lower().endswith(".csv")
            ])
    except Exception as e:
        logger.error(f"Error listando archivos GitHub: {e}")
    return []


def _push_contenido_api(ruta: str, datos: bytes, msg: str) -> Tuple[bool, str]:
    try:
        url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{ruta}"
        check = requests.get(f"{url}?ref={GITHUB_BRANCH}", headers=_headers_gh(), timeout=15)
        sha   = check.json().get("sha") if check.status_code == 200 else None
        payload = {"message": msg, "content": base64.b64encode(datos).decode(), "branch": GITHUB_BRANCH}
        if sha: payload["sha"] = sha
        r = requests.put(url, json=payload, headers=_headers_gh(), timeout=30)
        return (True, "OK") if r.status_code in (200, 201) else (False, f"HTTP {r.status_code}: {r.text[:300]}")
    except Exception as e:
        return False, str(e)


def _push_blob_git_data_api(ruta: str, datos: bytes, msg: str) -> Tuple[bool, str]:
    """Git Data API para archivos grandes (sin límite de 1 MB)."""
    base = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}"
    h    = _headers_gh()
    try:
        r_blob = requests.post(f"{base}/git/blobs",
            json={"content": base64.b64encode(datos).decode(), "encoding": "base64"},
            headers=h, timeout=60)
        if r_blob.status_code not in (200, 201):
            return False, f"Error blob: {r_blob.text[:300]}"
        sha_blob   = r_blob.json()["sha"]
        r_ref      = requests.get(f"{base}/git/ref/heads/{GITHUB_BRANCH}", headers=h, timeout=20)
        sha_commit = r_ref.json()["object"]["sha"]
        r_cm       = requests.get(f"{base}/git/commits/{sha_commit}", headers=h, timeout=20)
        sha_tree   = r_cm.json()["tree"]["sha"]
        r_tree     = requests.post(f"{base}/git/trees",
            json={"base_tree": sha_tree, "tree": [{"path": ruta, "mode": "100644", "type": "blob", "sha": sha_blob}]},
            headers=h, timeout=30)
        sha_tree_n = r_tree.json()["sha"]
        r_nc       = requests.post(f"{base}/git/commits",
            json={"message": msg, "tree": sha_tree_n, "parents": [sha_commit]},
            headers=h, timeout=30)
        sha_nc     = r_nc.json()["sha"]
        r_upd      = requests.patch(f"{base}/git/refs/heads/{GITHUB_BRANCH}",
            json={"sha": sha_nc}, headers=h, timeout=20)
        return (True, "OK") if r_upd.status_code in (200, 201) else (False, f"Error ref: {r_upd.text[:300]}")
    except Exception as e:
        return False, str(e)


@st.cache_data(ttl=600, show_spinner=False, max_entries=3)
def interpretar_csv_pegado(texto: str) -> pd.DataFrame:
    texto = texto.lstrip("\ufeff").strip("\r\n")
    if not texto.strip():
        raise ValueError("Pega los encabezados y al menos una fila de datos.")
    try:
        sep = csv.Sniffer().sniff(texto[:65536], delimiters=",;\t").delimiter
    except csv.Error:
        sep = "\t"
    df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    return df


def _parsear_texto_pegado(txt: str) -> Optional[pd.DataFrame]:
    try:
        return interpretar_csv_pegado(txt)
    except Exception:
        return None


@st.cache_data(ttl=180, show_spinner=False)
def inventario_repositorio_github() -> Dict[str, str]:
    url  = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/git/trees/{quote(GITHUB_BRANCH, safe='')}"
    resp = requests.get(url, params={"recursive": "1"}, headers=_headers_gh(), timeout=25)
    resp.raise_for_status()
    data = resp.json()
    if data.get("truncated"):
        raise ValueError("Listado GitHub incompleto.")
    return {item["path"]: item["type"] for item in data.get("tree", [])}


def url_contenido_github(ruta: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{quote(ruta, safe='/')}"


def leer_bytes_github(ruta: str) -> bytes:
    meta = requests.get(url_contenido_github(ruta), params={"ref": GITHUB_BRANCH},
                        headers=_headers_gh(), timeout=25)
    meta.raise_for_status()
    sha = meta.json()["sha"]
    res = requests.get(
        f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/git/blobs/{sha}",
        headers={**_headers_gh(), "Accept": "application/vnd.github.raw+json"}, timeout=45)
    res.raise_for_status()
    return res.content


def _regenerar_parquet_consolidado(nombre_csv: str, df_raw: pd.DataFrame) -> pd.DataFrame:
    inventario_repositorio_github.clear()
    inventario = inventario_repositorio_github()
    prefijo    = f"{GITHUB_FOLDER.strip('/')}/"
    ruta_cons  = prefijo + "datos_consolidados.parquet"
    nuevo = df_raw.copy(); nuevo["Archivo_Origen"] = nombre_csv
    if ruta_cons in inventario:
        anterior = pd.read_parquet(io.BytesIO(leer_bytes_github(ruta_cons)))
        if "Archivo_Origen" in anterior.columns:
            anterior = anterior[anterior["Archivo_Origen"] != nombre_csv]
        return pd.concat([anterior, transformar_dataset_completo(nuevo)], ignore_index=True)
    return transformar_dataset_completo(nuevo)


def guardar_csv_en_carpeta(ruta: str, df: pd.DataFrame, agregar: bool) -> pd.DataFrame:
    url = url_contenido_github(ruta)
    res = requests.get(url, params={"ref": GITHUB_BRANCH}, headers=_headers_gh(), timeout=25)
    sha  = None; final = df.copy()
    if agregar:
        res.raise_for_status()
        meta = res.json()
        if meta.get("type") != "file":
            raise ValueError("El destino no es un archivo CSV.")
        sha = meta["sha"]
        raw = requests.get(
            f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/git/blobs/{sha}",
            headers={**_headers_gh(), "Accept": "application/vnd.github.raw+json"}, timeout=45)
        raw.raise_for_status()
        try:    texto = raw.content.decode("utf-8-sig")
        except: texto = raw.content.decode("latin1")
        previo = interpretar_csv_pegado(texto)
        if set(previo.columns) != set(df.columns):
            raise ValueError("Las columnas pegadas no coinciden con el archivo existente.")
        final = pd.concat([previo, df[previo.columns]], ignore_index=True)
        claves = [detectar_columna_por_patrones(list(final.columns), a)
                  for a in [LISTA_ALIAS_ORDEN, LISTA_ALIAS_CUENTA, LISTA_ALIAS_OT, LISTA_ALIAS_TIPO]]
        if all(claves):
            final = final.drop_duplicates(subset=claves, keep="last")
        else:
            final = final.drop_duplicates(keep="last")
    elif res.status_code == 200:
        raise ValueError("Ya existe un archivo con ese nombre. Selecciona actualizar o escribe otro nombre.")
    elif res.status_code != 404:
        res.raise_for_status()

    csv_bytes = final.to_csv(index=False).encode("utf-8-sig")
    payload   = {"message": f"Actualizar CSV: {ruta}",
                 "content": base64.b64encode(csv_bytes).decode("ascii"),
                 "branch": GITHUB_BRANCH}
    if sha: payload["sha"] = sha
    r = requests.put(url, json=payload, headers=_headers_gh(), timeout=60)
    if r.status_code == 409:
        raise ValueError("El archivo cambió durante la carga. Actualiza el listado e intenta de nuevo.")
    r.raise_for_status()
    datos       = r.json()
    commit_sha  = datos["commit"]["sha"]
    esperado    = hashlib.sha1(b"blob " + str(len(csv_bytes)).encode() + b"\0" + csv_bytes).hexdigest()
    if datos["content"]["sha"] != esperado:
        raise ValueError("La verificación del archivo guardado falló. Revisa GitHub.")
    final.attrs["guardado_github"] = {
        "ruta": ruta, "filas": len(final),
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "url": f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/blob/{commit_sha}/{quote(ruta, safe='/')}",
    }
    return final


# ==============================================================================
# 9. CSS ENTERPRISE (FUENTE ARIAL, CERO EMOJIS, PALETA UNIFICADA)
# ==============================================================================

def inyectar_estilos_css_enterprise() -> None:
    p = PALETA_COLOR
    css = f"""
    <style>
    html, body, [class*="css"], .stApp {{
        font-family: Arial, "Helvetica Neue", Helvetica, sans-serif !important;
        background-color: #F8FAFC !important;
        color: #000000 !important;
    }}

    /* Sidebar */
    [data-testid="stSidebar"] {{
        background-color: {p["azul_noche"]} !important;
        min-width: 300px !important;
    }}
    [data-testid="stSidebar"] * {{ color: #FFFFFF !important; }}

    /* Header principal */
    .main-header-enterprise {{
        background: linear-gradient(135deg, {p["azul_noche"]} 0%, {p["azul_marina"]} 100%);
        padding: 22px 28px;
        border-radius: 12px;
        border-bottom: 3px solid {p["turquesa_cyan"]};
        color: #FFFFFF !important;
        margin-bottom: 18px;
        box-shadow: 0 6px 16px -4px rgba(11,25,44,0.35);
    }}
    .main-header-enterprise h1 {{
        font-family: Arial, sans-serif !important;
        font-size: 22px !important;
        font-weight: 800 !important;
        letter-spacing: 0.5px !important;
        color: #FFFFFF !important;
        margin: 0 0 4px 0 !important;
    }}
    .main-header-enterprise p {{
        font-family: Arial, sans-serif !important;
        font-size: 13px !important;
        font-weight: 600 !important;
        color: {p["turquesa_cyan"]} !important;
        margin: 0 !important;
    }}

    /* KPI Cards */
    .kpi-card-enterprise {{
        background: linear-gradient(135deg, {p["azul_noche"]} 0%, {p["azul_marina"]} 80%);
        border-radius: 10px;
        padding: 16px 18px;
        border: 1px solid {p["azul_marina"]};
        box-shadow: 0 4px 10px -2px rgba(11,25,44,0.3);
    }}
    .kpi-card-title {{
        font-family: Arial, sans-serif;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 1px;
        color: {p["gris_borde"]};
        text-transform: uppercase;
        margin-bottom: 6px;
    }}
    .kpi-card-value {{
        font-family: Arial, sans-serif;
        font-size: 28px;
        font-weight: 800;
        color: {p["turquesa_cyan"]};
        line-height: 1.1;
    }}
    .kpi-card-subtitle {{
        font-family: Arial, sans-serif;
        font-size: 10px;
        font-weight: 400;
        color: {p["gris_borde"]};
        margin-top: 4px;
    }}

    /* Tabs (navegación) */
    div[data-testid="stTabs"] {{
        background-color: #0f172a !important;
        padding: 6px !important;
        border-radius: 10px !important;
        border: 1px solid #1e293b !important;
    }}
    div[data-testid="stTabs"] > div[role="tablist"] {{
        gap: 6px !important;
        background-color: transparent !important;
        border-bottom: none !important;
    }}
    div[data-testid="stTabs"] button[role="tab"] {{
        background-color: #1e293b !important;
        border: 1px solid #334155 !important;
        border-radius: 7px !important;
        padding: 8px 16px !important;
        transition: all 0.2s ease !important;
        font-family: Arial, sans-serif !important;
    }}
    div[data-testid="stTabs"] button[role="tab"] p,
    div[data-testid="stTabs"] button[role="tab"] span {{
        color: #94a3b8 !important;
        font-size: 13px !important;
        font-weight: 600 !important;
        font-family: Arial, sans-serif !important;
    }}
    div[data-testid="stTabs"] button[role="tab"]:hover {{
        background-color: #334155 !important;
    }}
    div[data-testid="stTabs"] button[role="tab"]:hover p {{ color: #f8fafc !important; }}
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {{
        background-color: {p["azul_marina"]} !important;
        border-color: {p["turquesa_cyan"]} !important;
        box-shadow: 0 3px 10px rgba(0,210,200,0.25) !important;
    }}
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p {{
        color: #ffffff !important; font-weight: 700 !important;
    }}
    div[data-testid="stTabs"] div[data-baseweb="tab-highlight"] {{ display: none !important; }}

    /* Matriz / tablas header */
    .matrix-title-card {{
        background: #1e293b;
        padding: 10px 14px;
        border-radius: 7px;
        margin: 12px 0;
        border: 1px solid #334155;
    }}
    .matrix-title-card b {{ color: #f8fafc; font-family: Arial, sans-serif; font-size: 14px; font-weight: 700; }}
    .matrix-title-card p  {{ color: #94a3b8; font-family: Arial, sans-serif; font-size: 11px; margin: 2px 0 0 0; }}

    /* Inputs en sidebar */
    div[data-widget="stMultiSelect"] label,
    div[data-widget="stSelectbox"]   label {{ color: #f1f5f9 !important; font-weight: 600 !important; font-size: 12px !important; }}
    div[data-testid="stTabs"] .stMarkdown p,
    div[data-testid="stTabs"] .stMarkdown h1,
    div[data-testid="stTabs"] .stMarkdown h2,
    div[data-testid="stTabs"] .stMarkdown h3 {{ color: #f8fafc !important; }}

    /* Textarea / inputs */
    div[data-testid="stTextArea"] textarea, div[data-testid="stTextInput"] input {{
        background-color: #0f172a !important; color: #f8fafc !important;
        border: 1px solid #334155 !important; border-radius: 7px !important;
    }}

    /* Formulario de carga (contraste claro) */
    .st-key-formulario_carga, .st-key-formulario_carga * {{ color: #000000 !important; }}
    .st-key-formulario_carga div[data-testid="stTextArea"] textarea,
    .st-key-formulario_carga div[data-testid="stTextInput"] input,
    .st-key-formulario_carga [data-baseweb="select"] > div {{
        background-color: #ffffff !important; color: #000000 !important;
        -webkit-text-fill-color: #000000 !important; border-color: #64748b !important;
    }}

    /* Botones */
    div.stButton > button[kind="primary"] {{
        background-color: {p["azul_marina"]} !important; color: #FFFFFF !important;
        border: 1px solid {p["azul_marina"]} !important; border-radius: 7px !important;
        font-family: Arial, sans-serif !important; font-weight: 700 !important;
        font-size: 13px !important;
    }}
    div.stButton > button[kind="primary"]:hover {{
        background-color: {p["azul_noche"]} !important;
    }}
    div.stButton > button[kind="secondary"], div.stButton > button:not([kind="primary"]) {{
        background-color: #FFFFFF !important; color: {p["azul_marina"]} !important;
        border: 1px solid {p["gris_borde"]} !important; border-radius: 7px !important;
        font-family: Arial, sans-serif !important; font-weight: 600 !important;
        font-size: 13px !important;
    }}

    /* Desactivar zoom táctil en gráficos */
    .js-plotly-plot {{ touch-action: pan-y !important; }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


# ==============================================================================
# 10. VISTAS
# ==============================================================================

def abrir_captura_completa():
    st.session_state["seccion_principal"] = "Capturar / Actualizar datos"


def renderizar_pestana_polizas_cuadrillas(df_folios: pd.DataFrame, df_raw: pd.DataFrame, dimension_sel: str) -> None:
    kpis = extraer_metricas_kpi_totales(df_folios, df_raw, dimension_sel)

    sub_tab1, sub_tab2, sub_tab3, sub_tab4, sub_tab5 = st.tabs([
        "Evolución y Productividad",
        "Desglose por Pólizas",
        "Ranking de Cuadrillas / Técnicos",
        "Descarga de Reportes",
        "Cargar Datos"
    ])

    with sub_tab5:
        st.button("Abrir panel de carga de datos", on_click=abrir_captura_completa, key="abrir_captura_ancha")

    # --- SUBTAB 1: EVOLUCIÓN & PRODUCTIVIDAD ---
    with sub_tab1:
        # KPI Cards
        st.markdown(f"""
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px;">
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">Órdenes Totales</div>
                <div class="kpi-card-value">{kpis.total_eventos:,}</div>
                <div class="kpi-card-subtitle">Todos los tipos de evento</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">Técnicos Activos</div>
                <div class="kpi-card-value">{kpis.total_usuarios:,}</div>
                <div class="kpi-card-subtitle">Usuarios únicos en el período</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">Días Operativos</div>
                <div class="kpi-card-value">{kpis.dias_operativos}</div>
                <div class="kpi-card-subtitle">Días calendario del período</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">Productividad / Día</div>
                <div class="kpi-card-value" style="color:{PALETA_COLOR['turquesa_cyan']} !important;">{kpis.productividad_diaria}</div>
                <div class="kpi-card-subtitle">Eventos por técnico por día</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        mask_g = df_folios[dimension_sel].notnull() & (
            ~df_folios[dimension_sel].astype(str).str.lower().isin(["nan","none","null",""])
        )
        fig_ev = generar_figura_evolucion_temporal(df_folios[mask_g], dimension_sel)
        st.plotly_chart(fig_ev, use_container_width=True, key="grafico_evolucion_temporal",
                        config={"displayModeBar": False, "scrollZoom": False})

        nom_dim = {"FECHA_TRUNCADA":"Día","SEMANA_DIM":"Semana","MES_DIM":"Mes","AÑO_DIM":"Año"}.get(dimension_sel,"Período")
        st.markdown(f"""
        <div class="matrix-title-card">
            <b>CUADRILLAS / TÉCNICOS ÚNICOS POR {nom_dim.upper()} Y PROVEEDOR</b>
            <p>Conteo de usuarios técnicos con actividad registrada por período.</p>
        </div>""", unsafe_allow_html=True)

        if not df_folios.empty:
            df_mz = pd.pivot_table(df_folios, index="Empresa", columns=dimension_sel,
                                   values="Usuario_Tecnico", aggfunc="nunique", fill_value=0)
            cols_r = list(df_mz.columns)
            if dimension_sel == "FECHA_TRUNCADA":
                cols_ord = sorted(cols_r, key=lambda x: pd.to_datetime(x, format="%d.%m.%Y", errors="coerce") or x)
            elif dimension_sel == "MES_DIM":
                cols_ord = [m for m in LISTA_ORDENADA_MESES if m in cols_r]
            else:
                cols_v   = [c for c in cols_r if str(c).lower() not in ["nan","none","null",""]]
                cols_ord = sorted(cols_v, key=lambda x: _num_sem(x))

            df_mz = df_mz[cols_ord]
            vals  = df_mz.to_numpy()
            df_mz["Tendencia"]        = vals.tolist()
            df_mz["Promedio Período"] = np.round(vals.mean(axis=1), 1)

            fila_tot = df_folios.groupby(dimension_sel)["Usuario_Tecnico"].nunique().reindex(cols_ord).fillna(0).astype(int)
            row_dict = dict(zip(cols_ord, fila_tot.values))
            row_dict["Tendencia"]        = fila_tot.tolist()
            row_dict["Promedio Período"] = round(float(fila_tot.mean()), 1)
            df_mz = pd.concat([df_mz, pd.DataFrame([row_dict], index=["TOTAL GENERAL"])])

            cols_fin = ["Tendencia"] + cols_ord + ["Promedio Período"]
            st.dataframe(
                df_mz[cols_fin].reset_index().rename(columns={"index":"Empresa"}),
                column_config={
                    "Empresa":          st.column_config.Column(width="medium", pinned=True),
                    "Tendencia":        st.column_config.LineChartColumn(width="small", y_min=0, pinned=True),
                    "Promedio Período": st.column_config.NumberColumn(format="%.1f"),
                },
                use_container_width=True, hide_index=True, height=300
            )

            st.markdown("---")
            c1, c2 = st.columns(2)
            with c1:
                df_pol = (df_folios.groupby("Nombre_Poliza", observed=True)
                          .size().reset_index(name="Total").sort_values("Total"))
                fig_p  = px.bar(df_pol, x="Total", y="Nombre_Poliza", orientation="h",
                                text="Total", title="<b>VOLUMEN POR TIPO DE PÓLIZA</b>",
                                color_discrete_sequence=[PALETA_COLOR["azul_marina"]])
                fig_p.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                    font=dict(family="Arial,sans-serif", size=11, color="#E2E8F0"),
                                    title=dict(font=dict(color="#FFFFFF", size=13)),
                                    xaxis=dict(showgrid=True, gridcolor="#334155",
                                               tickfont=dict(color="#E2E8F0", size=10)),
                                    yaxis=dict(tickfont=dict(color="#E2E8F0", size=10)))
                fig_p.update_traces(textposition="outside", textfont=dict(color="#FFFFFF", size=10))
                st.plotly_chart(fig_p, use_container_width=True, config={"displayModeBar": False})

            with c2:
                df_ev = (df_folios.groupby("Tipo_Orden", observed=True)
                         .size().reset_index(name="Total").sort_values("Total").tail(10))
                fig_e = px.bar(df_ev, x="Total", y="Tipo_Orden", orientation="h",
                               text="Total", title="<b>TOP 10 TIPOS DE EVENTO</b>",
                               color_discrete_sequence=[PALETA_COLOR["turquesa_cyan"]])
                fig_e.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                    font=dict(family="Arial,sans-serif", size=11, color="#E2E8F0"),
                                    title=dict(font=dict(color="#FFFFFF", size=13)),
                                    xaxis=dict(showgrid=True, gridcolor="#334155",
                                               tickfont=dict(color="#E2E8F0", size=10)),
                                    yaxis=dict(tickfont=dict(color="#E2E8F0", size=10)))
                fig_e.update_traces(textposition="outside", textfont=dict(color="#FFFFFF", size=10))
                st.plotly_chart(fig_e, use_container_width=True, config={"displayModeBar": False})

    # --- SUBTAB 2: DESGLOSE PÓLIZAS ---
    with sub_tab2:
        st.markdown("### Resumen Operativo por Tipo de Póliza")
        if not df_folios.empty:
            df_rp = (df_folios.groupby(["Codigo_Poliza","Nombre_Poliza"], observed=True)
                     .agg(Total_Eventos=("FOLIO_KEY","count"),
                          Tecnicos_Unicos=("Usuario_Tecnico","nunique"),
                          Dias_Operativos=("FECHA_TRUNCADA","nunique"))
                     .reset_index())
            df_rp["Productividad_Promedio"] = np.vectorize(calcular_indice_productividad_diaria)(
                df_rp["Total_Eventos"].to_numpy(),
                df_rp["Tecnicos_Unicos"].to_numpy(),
                df_rp["Dias_Operativos"].to_numpy()
            )
            st.dataframe(df_rp, use_container_width=True, hide_index=True)

    # --- SUBTAB 3: RANKING ---
    with sub_tab3:
        st.markdown("### Ranking de Productividad por Técnico / Cuadrilla")
        if not df_folios.empty:
            df_rk = (df_folios.groupby(["Usuario_Tecnico","Codigo_Poliza","Nombre_Poliza","Empresa"], observed=True)
                     .agg(Eventos_Totales=("FOLIO_KEY","count"),
                          Dias_Activos=("FECHA_TRUNCADA","nunique"))
                     .reset_index())
            df_rk["Productividad_Diaria"] = np.vectorize(calcular_indice_productividad_diaria)(
                df_rk["Eventos_Totales"].to_numpy(), 1, df_rk["Dias_Activos"].to_numpy()
            )
            df_rk = df_rk.sort_values("Productividad_Diaria", ascending=False)
            st.dataframe(df_rk, use_container_width=True, hide_index=True, height=420)

    # --- SUBTAB 4: DESCARGA ---
    with sub_tab4:
        st.markdown("### Descarga de Reportes")
        st.download_button(
            "Descargar Dataset (CSV)",
            df_folios.to_csv(index=False).encode("utf-8"),
            f"Reporte_{ANIO_BASE_ESTRICTO}.csv", "text/csv"
        )


# ==============================================================================
# 11. MÓDULO DE CARGA A GITHUB
# ==============================================================================

def renderizar_modulo_carga_github():
    st.markdown("""<style>
    .st-key-formulario_carga [data-testid="stSelectbox"] div,
    .st-key-formulario_carga [data-testid="stSelectbox"] input,
    .st-key-formulario_carga [data-testid="stSelectbox"] [role="combobox"] {{
        background: #ffffff !important; color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
    }}
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="listbox"],
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="option"] {{
        background-color: #ffffff !important; color: #000000 !important;
    }}
    </style>""", unsafe_allow_html=True)
    with st.container(key="formulario_carga"):
        _renderizar_formulario_carga_github()


def _renderizar_formulario_carga_github():
    st.markdown("### Panel de Carga de Datos")
    st.caption(f"Versión {VERSION_SISTEMA} · Repositorio: {GITHUB_USER}/{GITHUB_REPO} · Rama: {GITHUB_BRANCH}")

    resultado_previo = st.session_state.get("resultado_captura")
    if isinstance(resultado_previo, dict):
        st.success(f"Último guardado verificado: {resultado_previo['ruta']} — {resultado_previo['filas']:,} registros — {resultado_previo['fecha']}")
        st.link_button("Ver el CSV en GitHub", resultado_previo["url"])

    contenido_txt = st.text_area(
        "Pega aquí el CSV completo o celdas copiadas desde Excel (incluir encabezados):",
        height=360, key="contenido_txt_carga_v2",
        placeholder="Cuenta\tTicket\tOS\tOT\tTipo\tDistrito\tFecha creacion FFM\tFecha termino\tEstatus",
    )
    df_preview = _parsear_texto_pegado(contenido_txt) if contenido_txt.strip() else None

    if contenido_txt.strip() and df_preview is None:
        st.error("No se pudo interpretar el texto pegado. Verifica que incluya la fila de encabezados.")

    if df_preview is not None and not df_preview.empty:
        st.markdown(f"**Vista previa:** {len(df_preview):,} filas · {len(df_preview.columns)} columnas")
        st.dataframe(df_preview.head(8), use_container_width=True, hide_index=True, height=200)

        col_cr = detectar_columna_por_patrones(list(df_preview.columns), LISTA_ALIAS_CREACION)
        col_tr = detectar_columna_por_patrones(list(df_preview.columns), LISTA_ALIAS_TERMINO)
        c1, c2, c3 = st.columns(3)
        c1.metric("Filas detectadas", f"{len(df_preview):,}")
        c2.metric("Fechas de creación válidas",
                  f"{parsear_columna_fecha_robusta(df_preview[col_cr]).notna().sum():,}" if col_cr else "No detectada")
        c3.metric("Fechas de término válidas",
                  f"{parsear_columna_fecha_robusta(df_preview[col_tr]).notna().sum():,}" if col_tr else "No detectada")

    try:
        inventario = inventario_repositorio_github()
    except Exception as e:
        st.error(f"No se pudo listar el repositorio: {e}")
        return

    prefijo   = f"{GITHUB_FOLDER.strip('/')}/" if GITHUB_FOLDER.strip("/") else ""
    carpeta   = None; nombre_csv = None; agregar = False

    carpetas_disponibles = sorted({"/".join(k.split("/")[:-1])
                                   for k, t in inventario.items()
                                   if t == "blob" and k.lower().endswith(".csv")
                                   and k.startswith(prefijo)})
    if not carpetas_disponibles:
        carpetas_disponibles = [GITHUB_FOLDER.strip("/")]

    carpeta = st.selectbox("Carpeta de destino:", carpetas_disponibles, key="carpeta_destino_sel")

    archivos_csv = sorted([
        k[len(prefijo):] for k, t in inventario.items()
        if t == "blob" and k.startswith(prefijo) and "/" not in k[len(prefijo):]
        and k.lower().endswith(".csv")
    ])
    modos   = ["Crear un CSV nuevo"] + (["Actualizar un CSV existente"] if archivos_csv else [])
    modo    = st.radio("Destino del archivo:", modos, horizontal=True, key="modo_carga_sel")
    agregar = (modo == "Actualizar un CSV existente")

    if agregar:
        nombre_csv = st.selectbox("CSV a actualizar:", archivos_csv, key="csv_existente_sel")
        st.caption("Los registros anteriores se conservan; los duplicados (OS + Cuenta + OT + Tipo) se reemplazan por la versión más reciente.")
    else:
        nombre_inp = st.text_input("Nombre del CSV nuevo:", key="nombre_csv_nuevo",
                                   placeholder="CIERRE DIARIO SEM 38 2026.csv").strip()
        if nombre_inp:
            candidato = nombre_inp if nombre_inp.lower().endswith(".csv") else f"{nombre_inp}.csv"
            if prefijo + candidato in inventario:
                st.error("Ese nombre ya existe. Elige 'Actualizar' o escribe otro nombre.")
            else:
                nombre_csv = candidato

    destino = f"{carpeta}/{nombre_csv}" if carpeta and nombre_csv else None
    if destino:
        st.info(f"Destino: `{GITHUB_REPO}/{destino}`")

    clave = st.text_input("Clave de autorización:", type="password", key="token_auth_carga_v2")

    puede_guardar = df_preview is not None and destino is not None
    if st.button("Guardar CSV en GitHub", type="primary", disabled=not puede_guardar, key="guardar_captura"):
        if not GITHUB_TOKEN:
            st.error("Configura github.token en los secretos de Streamlit.")
            return
        if clave != st.secrets.get("UPLOAD_PASSWORD", "admin123"):
            st.error("Clave de autorización incorrecta.")
            return
        with st.spinner("Guardando y verificando en GitHub..."):
            try:
                final = guardar_csv_en_carpeta(destino, df_preview, agregar)
            except Exception as exc:
                st.error(f"No se confirmó el guardado: {exc}")
                return

        confirmado = final.attrs["guardado_github"]
        st.session_state["resultado_captura"] = confirmado
        st.success(f"Guardado y verificado: {destino} — {len(final):,} registros.")
        st.link_button("Ver el CSV en GitHub", confirmado["url"])

        if carpeta and carpeta.rstrip("/") == GITHUB_FOLDER.strip("/"):
            with st.spinner("Regenerando el dataset consolidado..."):
                try:
                    cons = _regenerar_parquet_consolidado(nombre_csv, final)
                    if cons is None or cons.empty:
                        raise ValueError("Consolidado vacío.")
                    buf = io.BytesIO()
                    cons.to_parquet(buf, index=False)
                    rp = "/".join(p for p in (GITHUB_FOLDER.strip("/"), "datos_consolidados.parquet") if p)
                    ok, det = _push_blob_git_data_api(rp, buf.getvalue(), f"Consolidado tras {nombre_csv}")
                    if ok:
                        st.success("Dataset consolidado actualizado. El dashboard reflejará los datos de inmediato.")
                    else:
                        raise ValueError(det)
                except Exception as exc:
                    st.warning(f"El CSV sí quedó guardado, pero no se pudo regenerar el consolidado: {exc}")

        st.cache_data.clear()
        listar_archivos_semanales_github.clear()
        st.rerun()


# ==============================================================================
# 12. MÓDULO DE REINCIDENCIAS
# ==============================================================================

@st.dialog("Historial de Reincidencias del Técnico", width="large")
def mostrar_modal_detalle_usuario(df_usuario: pd.DataFrame, usuario_nom: str):
    st.markdown(f"**Técnico origen:** {usuario_nom}")
    if df_usuario.empty:
        st.info("Sin folios reincidentes para este técnico.")
        return
    df_m = df_usuario.copy()
    if "TIPO_2" not in df_m.columns:
        df_m["TIPO_2"] = df_m.get("Causa_Origen","N/A")
    cols_p  = ["FOLIO_KEY","Cuenta_Cliente","Num_Semana_Archivo","TIPO_2","Falla_Nueva","Empresa_Origen_Reincidencia"]
    cols_pr = [c for c in cols_p if c in df_m.columns]
    renames = {"FOLIO_KEY":"Folio Reincidente","Cuenta_Cliente":"Cuenta",
               "Num_Semana_Archivo":"Semana","TIPO_2":"Tipo / Causa Origen",
               "Falla_Nueva":"Falla (Soporte)","Empresa_Origen_Reincidencia":"Empresa Técnico"}
    df_show = df_m[cols_pr].rename(columns=renames)
    st.dataframe(df_show, use_container_width=True, hide_index=True, height=380)
    st.download_button(
        f"Descargar historial de {usuario_nom} (CSV)",
        df_show.to_csv(index=False).encode("utf-8"),
        f"Reincidencias_{usuario_nom}.csv", "text/csv", use_container_width=True
    )


@st.dialog("Detalle de Reincidencias por Cuenta de Cliente", width="large")
def mostrar_modal_detalle_cuenta(df_cuenta: pd.DataFrame, cuenta_id: str):
    """
    Pop-up con el historial completo de reincidencias de una cuenta específica.
    Muestra todos los folios reincidentes, semanas, técnico origen, tipo reincidente
    (resuelto con la misma regla: N/A → Tipo_Orden; distinto → "Soporte"), falla y causa.
    """
    st.markdown(f"**Cuenta:** `{cuenta_id}`  ·  **{len(df_cuenta):,}** visitas reincidentes registradas")
    if df_cuenta.empty:
        st.info("Sin folios reincidentes para esta cuenta.")
        return

    df_m = df_cuenta.copy()

    # Resolver tipo reincidente con la misma regla del gráfico
    def _tipo_rein_display(row) -> str:
        t2 = str(row.get("TIPO_2", "")).strip().upper()
        if t2 in ["", "N/A", "NAN", "NONE", "NULL"]:
            return str(row.get("Tipo_Orden", "SIN TIPO")).strip()
        return "SOPORTE"

    df_m["Tipo Reincidente"] = df_m.apply(_tipo_rein_display, axis=1)

    cols_display = {
        "FOLIO_KEY":                      "Folio",
        "Num_Semana_Archivo":             "Semana",
        "Tipo Reincidente":               "Tipo Reincidente",
        "TIPO_2":                         "Causa Origen (TIPO_2)",
        "Falla_Nueva":                    "Falla (Soporte)",
        "Usuario_Origen_Reincidencia":    "Técnico Origen",
        "Empresa_Origen_Reincidencia":    "Empresa Origen",
        "SEMANA_DIM":                     "Sem. Dim",
    }
    cols_ok = {k: v for k, v in cols_display.items() if k in df_m.columns}
    df_show  = df_m[list(cols_ok.keys())].rename(columns=cols_ok)

    # Mini-resumen por tipo reincidente
    resumen = df_m["Tipo Reincidente"].value_counts().reset_index()
    resumen.columns = ["Tipo Reincidente", "Visitas"]
    c1, c2 = st.columns([0.35, 0.65])
    with c1:
        st.markdown("**Distribución por tipo:**")
        st.dataframe(resumen, hide_index=True, use_container_width=True, height=180)
    with c2:
        st.markdown("**Historial de folios:**")
        st.dataframe(df_show, hide_index=True, use_container_width=True, height=340)

    st.download_button(
        f"Descargar historial de cuenta {cuenta_id} (CSV)",
        df_show.to_csv(index=False).encode("utf-8"),
        f"Reincidencias_Cuenta_{cuenta_id}.csv", "text/csv",
        use_container_width=True
    )


def renderizar_pestana_reincidencias_total(df_folios: pd.DataFrame, dimension_sel: str) -> None:
    st.markdown("### Módulo de Análisis de Reincidencias y Efectividad Operativa")

    if df_folios.empty:
        st.warning("Sin registros para los filtros seleccionados.")
        return

    for col_req, default in [
        ("Empresa_Origen_Reincidencia","N/A"),
        ("Usuario_Origen_Reincidencia","N/A"),
        ("Causa_Origen","N/A"), ("TIPO_2","N/A"),
        ("Falla_Nueva","N/A"), ("ES_CASO_ESPECIAL","NO")
    ]:
        if col_req not in df_folios.columns:
            df_folios[col_req] = default

    df_rein_base = df_folios[df_folios["ES_REINCIDENCIA"] == "SI"]

    st.markdown("#### Filtros de Reincidencias")
    col_f0, col_f1, col_f2, col_f3, col_f4 = st.columns(5)

    _opt = lambda df, col: sorted([x for x in df[col].unique() if str(x).upper() not in ["N/A","NAN","NONE",""]]) if col in df.columns else []

    with col_f0:
        st.markdown("**Distrito**")
        f_dist = st.multiselect("Distrito:", _opt(df_rein_base,"Distrito"), key="fltr_dist_rein", label_visibility="collapsed")
    with col_f1:
        st.markdown("**Empresa Reincidente**")
        f_emp = st.multiselect("Empresa:", _opt(df_rein_base,"Empresa_Origen_Reincidencia"), key="fltr_emp_rein", label_visibility="collapsed")
    with col_f2:
        st.markdown("**Técnico Reincidente**")
        f_tech = st.multiselect("Técnico:", _opt(df_rein_base,"Usuario_Origen_Reincidencia"), key="fltr_tech_rein", label_visibility="collapsed")
    with col_f3:
        st.markdown("**Tipo / Causa Origen**")
        f_tipo2 = st.multiselect("Tipo 2:", _opt(df_rein_base,"TIPO_2"), key="fltr_tipo2_rein", label_visibility="collapsed")
    with col_f4:
        st.markdown("**Falla Nueva (Soporte)**")
        f_falla = st.multiselect("Falla:", _opt(df_rein_base,"Falla_Nueva"), key="fltr_falla_rein", label_visibility="collapsed")

    df_fr = df_rein_base.copy()
    if f_dist:  df_fr = df_fr[df_fr["Distrito"].isin(f_dist)]
    if f_emp:   df_fr = df_fr[df_fr["Empresa_Origen_Reincidencia"].isin(f_emp)]
    if f_tech:  df_fr = df_fr[df_fr["Usuario_Origen_Reincidencia"].isin(f_tech)]
    if f_tipo2: df_fr = df_fr[df_fr["TIPO_2"].isin(f_tipo2)]
    if f_falla: df_fr = df_fr[df_fr["Falla_Nueva"].isin(f_falla)]

    # Denominador: solo los 4 tipos elegibles
    col_tipo_base = "Tipo_Orden" if "Tipo_Orden" in df_folios.columns else "TIPO"
    patron_efect  = r"INSTALA|SOPORTE|CAMBIO.*DOMICILIO|CAMBIO.*EQUIPO"
    mask_efect    = df_folios[col_tipo_base].astype(str).str.upper().str.contains(patron_efect, regex=True, na=False)
    df_base_efect = df_folios[mask_efect]

    total_base      = len(df_base_efect)
    total_rein_fil  = len(df_fr)

    # Tasa de reincidencia = Reincidentes / Completadas (4 tipos)
    tasa_reincidencia = round((total_rein_fil / total_base * 100), 2) if total_base > 0 else 0.0
    tasa_efectividad  = round(100.0 - tasa_reincidencia, 2)

    st.markdown(f"""
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px;">
        <div class="kpi-card-enterprise">
            <div class="kpi-card-title">Órdenes Completadas (Base)</div>
            <div class="kpi-card-value">{total_base:,}</div>
            <div class="kpi-card-subtitle">Inst / Sop / C. Dom / C. Eq</div>
        </div>
        <div class="kpi-card-enterprise">
            <div class="kpi-card-title">Reincidencias Identificadas</div>
            <div class="kpi-card-value" style="color:{PALETA_COLOR["naranja_desierto"]} !important;">{total_rein_fil:,}</div>
            <div class="kpi-card-subtitle">Visitas Soporte con antecedente &le;60 días</div>
        </div>
        <div class="kpi-card-enterprise">
            <div class="kpi-card-title">Tasa de Reincidencia</div>
            <div class="kpi-card-value" style="color:{PALETA_COLOR["amarillo_sol"]} !important;">{tasa_reincidencia}%</div>
            <div class="kpi-card-subtitle">Reincidentes / Total Completadas</div>
        </div>
        <div class="kpi-card-enterprise">
            <div class="kpi-card-title">Efectividad Operativa</div>
            <div class="kpi-card-value" style="color:{PALETA_COLOR["verde_montana"]} !important;">{tasa_efectividad}%</div>
            <div class="kpi-card-subtitle">Eventos sin reincidencia</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --------------------------------------------------------------------------
    # GRÁFICA: LÍNEA DE TIEMPO DE REINCIDENCIAS POR TIPO REINCIDENTE
    # Regla de dimensión "Tipo Reincidente":
    #   • Si TIPO_2 es N/A (o vacío) → se toma el valor de Tipo_Orden del evento
    #     actual (la visita de Soporte reincidente).
    #   • Si TIPO_2 tiene un valor distinto de N/A → se clasifica como "Soporte"
    #     (porque TIPO_2 viene del antecedente, lo que indica que la visita actual
    #     es un Soporte puro sobre un trabajo previo ya catalogado).
    # --------------------------------------------------------------------------
    if not df_fr.empty:
        st.markdown("""<div class="matrix-title-card" style="margin-top:16px;">
            <b>LÍNEA DE TIEMPO DE REINCIDENCIAS POR TIPO REINCIDENTE</b>
            <p>Distribución temporal de reincidencias según el tipo de evento que las originó.</p>
        </div>""", unsafe_allow_html=True)

        def _resolver_tipo_rein(row) -> str:
            t2 = str(row.get("TIPO_2", "")).strip().upper()
            if t2 in ["", "N/A", "NAN", "NONE", "NULL"]:
                return str(row.get("Tipo_Orden", "SIN TIPO")).strip()
            return "SOPORTE"

        df_fr_graf = df_fr.copy()
        df_fr_graf["_TIPO_REIN"] = df_fr_graf.apply(_resolver_tipo_rein, axis=1)

        # Ordenar eje X según la dimensión activa
        if dimension_sel == "SEMANA_DIM":
            dim_col = "SEMANA_DIM"
            cats_ord = sorted(df_fr_graf[dim_col].dropna().unique(), key=_num_sem)
        elif dimension_sel == "MES_DIM":
            dim_col  = "MES_DIM"
            cats_ord = [m for m in LISTA_ORDENADA_MESES if m in df_fr_graf[dim_col].unique()]
        elif dimension_sel == "FECHA_TRUNCADA":
            dim_col  = "FECHA_TRUNCADA"
            cats_ord = sorted(df_fr_graf[dim_col].dropna().unique())
        else:
            dim_col  = "AÑO_DIM"
            cats_ord = [str(ANIO_BASE_ESTRICTO)]

        df_graf_agg = (
            df_fr_graf.groupby([dim_col, "_TIPO_REIN"], observed=True)
            .size().reset_index(name="Reincidencias")
        )

        # Paleta de colores rotativa para los tipos
        colores_tipos = [
            PALETA_COLOR["naranja_desierto"], PALETA_COLOR["turquesa_cyan"],
            PALETA_COLOR["amarillo_sol"],     PALETA_COLOR["verde_montana"],
            PALETA_COLOR["azul_marina"],      "#A78BFA", "#F43F5E", "#06B6D4"
        ]
        tipos_unicos = sorted(df_graf_agg["_TIPO_REIN"].unique())
        mapa_color   = {t: colores_tipos[i % len(colores_tipos)] for i, t in enumerate(tipos_unicos)}

        fig_rein = go.Figure()
        for tipo in tipos_unicos:
            sub = df_graf_agg[df_graf_agg["_TIPO_REIN"] == tipo][[dim_col, "Reincidencias"]]
            sub = sub.set_index(dim_col).reindex(cats_ord, fill_value=0).reset_index()
            sub.columns = [dim_col, "Reincidencias"]
            fig_rein.add_trace(go.Bar(
                x=sub[dim_col], y=sub["Reincidencias"],
                name=tipo, marker_color=mapa_color[tipo],
                text=sub["Reincidencias"].where(sub["Reincidencias"] > 0),
                textposition="inside", textfont=dict(size=9, color="#FFFFFF"),
            ))
            tend = calcular_tendencia_lineal_robusta(sub["Reincidencias"].tolist())
            fig_rein.add_trace(go.Scatter(
                x=sub[dim_col], y=tend, name=f"Tendencia {tipo}",
                mode="lines", showlegend=False,
                line=dict(color=mapa_color[tipo], width=1.5, dash="dot"),
            ))

        fig_rein.update_layout(
            barmode="stack",
            paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
            font=dict(family="Arial, sans-serif", size=11, color="#000000"),
            margin=dict(l=50, r=30, t=40, b=120),
            title=dict(
                text=f"<b>REINCIDENCIAS POR TIPO ({dimension_sel})</b>",
                x=0.01, y=0.97,
                font=dict(size=12, color="#000000", family="Arial, sans-serif")
            ),
            legend=dict(
                orientation="h", yanchor="top", y=-0.35,
                xanchor="center", x=0.5,
                font=dict(size=10, color="#000000"),
                bgcolor="rgba(255,255,255,0.8)", bordercolor="#E2E8F0", borderwidth=1
            ),
            xaxis=dict(
                type="category", categoryorder="array", categoryarray=cats_ord,
                tickangle=-45, tickfont=dict(size=10, color="#000000"),
                showgrid=False, linecolor=PALETA_COLOR["azul_marina"],
            ),
            yaxis=dict(
                title=dict(text="Reincidencias", font=dict(size=11, color="#000000")),
                showgrid=True, gridcolor="#E2E8F0",
                tickfont=dict(size=10, color="#000000"),
            ),
            dragmode=False,
        )
        st.plotly_chart(fig_rein, use_container_width=True, key="grafico_rein_timeline",
                        config={"displayModeBar": False, "scrollZoom": False})

    st.markdown("""<div class="matrix-title-card">
        <b>EVALUACIÓN DE EFECTIVIDAD POR TÉCNICO ORIGEN</b>
        <p>Órdenes atendidas vs. reincidencias provocadas, agrupadas por técnico.</p>
    </div>""", unsafe_allow_html=True)

    if not df_fr.empty:
        col_tech_b = detectar_columna_por_patrones(list(df_folios.columns),
                         ["usuario_tecnico","tecnico","tech","usuario","atendio"]) or "Usuario_Tecnico"
        ev_x_tech  = df_base_efect.groupby(col_tech_b, observed=True).size().to_dict()

        _join = lambda x: " | ".join(sorted({str(v).strip() for v in x if pd.notna(v) and str(v).strip() not in ["","nan","None"]}))
        df_agt = (df_fr.groupby(["Usuario_Origen_Reincidencia","Empresa_Origen_Reincidencia"], observed=True)
                  .agg(Total_Reincidencias=("FOLIO_KEY","count"),
                       Causas_TIPO_2=("TIPO_2", _join),
                       Fallas_Nuevas=("Falla_Nueva", _join))
                  .reset_index())
        df_agt["Eventos_Atendidos"] = df_agt["Usuario_Origen_Reincidencia"].map(ev_x_tech).fillna(df_agt["Total_Reincidencias"])
        df_agt["Eventos_Atendidos"] = np.maximum(df_agt["Eventos_Atendidos"], df_agt["Total_Reincidencias"])
        atendidos = df_agt["Eventos_Atendidos"].to_numpy()
        reinci    = df_agt["Total_Reincidencias"].to_numpy()
        df_agt["Efectividad_%"] = np.where(atendidos > 0, np.round(((atendidos - reinci) / atendidos) * 100, 2), 0.0)
        df_agt = df_agt.rename(columns={
            "Usuario_Origen_Reincidencia":  "Técnico Reincidente (Origen)",
            "Empresa_Origen_Reincidencia":  "Empresa",
            "Eventos_Atendidos":            "Eventos Completados",
            "Total_Reincidencias":          "Total Reincidencias",
            "Efectividad_%":                "% Efectividad",
        }).sort_values("Total Reincidencias", ascending=False)

        col_t1, col_t2 = st.columns([0.75, 0.25])
        with col_t1:
            st.dataframe(df_agt[["Técnico Reincidente (Origen)","Empresa","Eventos Completados",
                                  "Total Reincidencias","% Efectividad","Causas_TIPO_2","Fallas_Nuevas"]],
                         use_container_width=True, hide_index=True, height=360,
                         column_config={"% Efectividad": st.column_config.NumberColumn(format="%.2f %%")})
        with col_t2:
            st.markdown("**Ver detalle ampliado:**")
            tech_lista = sorted(df_agt["Técnico Reincidente (Origen)"].unique())
            tech_sel   = st.selectbox("Seleccionar técnico:", tech_lista, key="sb_pop_tech")
            if st.button("Abrir detalle", use_container_width=True, key="btn_pop_tech"):
                mostrar_modal_detalle_usuario(
                    df_fr[df_fr["Usuario_Origen_Reincidencia"] == tech_sel], tech_sel
                )
    else:
        st.info("Sin datos de reincidencias para los filtros aplicados.")

    st.markdown("---")
    st.markdown("""<div class="matrix-title-card">
        <b>HISTORIAL POR CUENTA DE CLIENTE</b>
        <p>Busca una cuenta para consultar su historial completo de reincidencias. Haz clic en el botón de la fila para abrir el detalle.</p>
    </div>""", unsafe_allow_html=True)

    if not df_fr.empty:
        _join2 = lambda x: " | ".join(sorted({
            str(v).strip() for v in x
            if pd.notna(v) and str(v).strip() not in ["","nan","None","N/A"]
        }))

        df_cta = (df_fr.groupby("Cuenta_Cliente", observed=True)
                  .agg(Visitas_Reincidentes=("FOLIO_KEY","count"),
                       Semanas=("Num_Semana_Archivo",
                                lambda x: ", ".join(map(str, sorted({int(v) for v in x if str(v).isdigit()})))),
                       Tipos_Rein=("TIPO_2", lambda x: " | ".join(sorted({
                           str(v).strip() if str(v).strip().upper() not in ["","N/A","NAN","NONE","NULL"]
                           else "SOPORTE"
                           for v in x if pd.notna(v)
                       }))),
                       Fallas=("Falla_Nueva", _join2),
                       Tecnicos=("Usuario_Origen_Reincidencia", _join2))
                  .reset_index()
                  .sort_values("Visitas_Reincidentes", ascending=False)
                  .rename(columns={
                      "Cuenta_Cliente":    "Cuenta",
                      "Visitas_Reincidentes": "Reincidencias",
                      "Tipos_Rein":        "Tipos Reincidentes",
                      "Tecnicos":          "Técnicos Origen",
                  }))

        # --- Buscador ---
        col_bus, col_ord = st.columns([0.65, 0.35])
        with col_bus:
            texto_busq = st.text_input(
                "Buscar cuenta:", placeholder="Número de cuenta, técnico o tipo…",
                key="busq_cuenta_rein"
            )
        with col_ord:
            orden_col = st.selectbox(
                "Ordenar por:",
                ["Reincidencias", "Cuenta", "Tipos Reincidentes"],
                key="orden_cuenta_rein"
            )

        df_vis = df_cta.copy()
        if texto_busq.strip():
            mask = np.column_stack([
                df_vis[c].astype(str).str.lower().str.contains(
                    texto_busq.strip().lower(), na=False, regex=False
                )
                for c in df_vis.columns
            ]).any(axis=1)
            df_vis = df_vis[mask]

        asc_map = {"Reincidencias": False, "Cuenta": True, "Tipos Reincidentes": True}
        df_vis  = df_vis.sort_values(orden_col, ascending=asc_map.get(orden_col, False))

        st.caption(f"{len(df_vis):,} cuentas encontradas de {len(df_cta):,} totales")

        # --- Tabla + selector de cuenta para pop-up ---
        col_tbl, col_det = st.columns([0.70, 0.30])

        with col_tbl:
            st.dataframe(
                df_vis[["Cuenta","Reincidencias","Semanas","Tipos Reincidentes","Fallas","Técnicos Origen"]],
                use_container_width=True, hide_index=True, height=360,
                column_config={
                    "Cuenta":             st.column_config.Column(width="small",  pinned=True),
                    "Reincidencias":      st.column_config.NumberColumn(width="small"),
                    "Semanas":            st.column_config.Column(width="small"),
                    "Tipos Reincidentes": st.column_config.Column(width="medium"),
                    "Fallas":             st.column_config.Column(width="large"),
                    "Técnicos Origen":    st.column_config.Column(width="large"),
                }
            )

        with col_det:
            st.markdown("**Abrir detalle de cuenta:**")

            # Si hay búsqueda activa y solo queda una cuenta, la pre-selecciona
            cuentas_visibles = df_vis["Cuenta"].tolist()
            idx_default = 0
            if texto_busq.strip() and len(cuentas_visibles) == 1:
                idx_default = 0

            cuenta_sel = st.selectbox(
                "Seleccionar cuenta:", cuentas_visibles,
                index=idx_default, key="sel_cuenta_rein_popup"
            ) if cuentas_visibles else None

            if cuenta_sel:
                st.metric("Reincidencias de esta cuenta",
                          int(df_cta.loc[df_cta["Cuenta"] == cuenta_sel, "Reincidencias"].iloc[0]))

            if st.button("Ver historial completo", type="primary",
                         disabled=(cuenta_sel is None), use_container_width=True,
                         key="btn_popup_cuenta"):
                mostrar_modal_detalle_cuenta(
                    df_fr[df_fr["Cuenta_Cliente"] == cuenta_sel], str(cuenta_sel)
                )

        # Descarga completa
        st.download_button(
            "Descargar todas las cuentas (CSV)",
            df_cta.to_csv(index=False).encode("utf-8"),
            "Reincidencias_por_Cuenta.csv", "text/csv"
        )
    else:
        st.info("Sin historial de cuentas con reincidencia para mostrar.")


# ==============================================================================
# 13. FUNCIÓN PRINCIPAL (main)
# ==============================================================================

def main():
    inyectar_estilos_css_enterprise()

    col_hdr_left, col_hdr_right = st.columns([0.70, 0.30])
    with col_hdr_left:
        st.markdown(f"""
        <div class="main-header-enterprise">
            <h1>OPERACIONES — REGIÓN NORTE LA BAJA</h1>
            <p>Módulo Consolidado de Analítica, Pólizas y Control Técnico de Campo ({ANIO_BASE_ESTRICTO})</p>
        </div>""", unsafe_allow_html=True)

    with col_hdr_right:
        st.write("")
        btn_c1, btn_c2 = st.columns(2)
        with btn_c1:
            if st.button("Recargar", use_container_width=True, type="primary"):
                st.cache_data.clear(); st.rerun()
        with btn_c2:
            if st.button("Limpiar Caché", use_container_width=True, type="secondary"):
                st.cache_data.clear()
                for k in list(st.session_state.keys()):
                    del st.session_state[k]
                st.rerun()

    seccion = st.sidebar.radio(
        "Sección principal:",
        ["Consultar dashboard", "Capturar / Actualizar datos"],
        index=0, key="seccion_principal",
    )

    if seccion == "Capturar / Actualizar datos":
        renderizar_modulo_carga_github()
        return

    df_raw = ejecutar_pipeline_ingestion_datos()

    if df_raw.empty:
        st.error("Sin datos disponibles en GitHub. Verifica el token y la carpeta configurada.")
        st.info("Ve a 'Capturar / Actualizar datos' en el menú lateral para realizar la primera carga.")
        return

    # Sidebar de filtros
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Dimensión Temporal")
    dimension_sel = st.sidebar.radio(
        "Agrupar por:",
        ["SEMANA_DIM","FECHA_TRUNCADA","MES_DIM","AÑO_DIM"], index=0,
        format_func=lambda x: {"SEMANA_DIM":"Semana","FECHA_TRUNCADA":"Día","MES_DIM":"Mes","AÑO_DIM":"Año"}[x]
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Filtros Dinámicos")
    df_t = df_raw.copy()

    meses_disp = [m for m in LISTA_ORDENADA_MESES if m in df_t["MES_DIM"].unique()]
    sel_meses  = st.sidebar.multiselect("Mes:", meses_disp)
    if sel_meses:   df_t = df_t[df_t["MES_DIM"].isin(sel_meses)]

    sems_disp  = sorted(df_t["SEMANA_DIM"].dropna().unique(), key=_num_sem)
    sel_sems   = st.sidebar.multiselect("Semana:", sems_disp)
    if sel_sems:    df_t = df_t[df_t["SEMANA_DIM"].isin(sel_sems)]

    pols_disp  = sorted([k for k in df_t["Codigo_Poliza"].unique() if k in MAPEO_POLIZAS])
    opc_pol    = [f"{c} — {MAPEO_POLIZAS[c]}" for c in pols_disp]
    sel_pol    = st.sidebar.multiselect("Pólizas:", opc_pol)
    cods_pol   = [p.split(" — ")[0] for p in sel_pol]
    if cods_pol:    df_t = df_t[df_t["Codigo_Poliza"].isin(cods_pol)]

    tipos_ev   = sorted(df_t["Tipo_Orden"].unique())
    sel_tipos  = st.sidebar.multiselect("Tipo de Evento:", tipos_ev)
    if sel_tipos:   df_t = df_t[df_t["Tipo_Orden"].isin(sel_tipos)]

    distr_disp = sorted(df_t["Distrito"].unique())
    sel_distr  = st.sidebar.multiselect("Distrito / Zona:", distr_disp)
    if sel_distr:   df_t = df_t[df_t["Distrito"].isin(sel_distr)]

    emps_disp  = sorted(df_t["Empresa"].unique())
    sel_emps   = st.sidebar.multiselect("Proveedor / Empresa:", emps_disp)
    if sel_emps:    df_t = df_t[df_t["Empresa"].isin(sel_emps)]

    df_folios = df_t.drop_duplicates(subset=["FOLIO_KEY"], keep="first")

    tab1, tab2, tab3, tab4 = st.tabs([
        "Pólizas y Cuadrillas",
        "Reincidencias",
        "Cambios de Equipo",
        "Causa y Solución Soporte"
    ])

    with tab1:
        renderizar_pestana_polizas_cuadrillas(df_folios, df_raw, dimension_sel)
    with tab2:
        renderizar_pestana_reincidencias_total(df_folios, dimension_sel)
    with tab3:
        st.markdown("### Cambios de Equipo")
        st.info("Módulo en desarrollo.")
    with tab4:
        st.markdown("### Causa y Solución Soporte")
        st.info("Módulo en desarrollo.")


if __name__ == "__main__":
    main()
