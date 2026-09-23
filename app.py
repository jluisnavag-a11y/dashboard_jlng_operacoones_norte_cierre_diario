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

# Carpeta de ingresos de soporte (estructura separada, se integra al parquet consolidado)
GITHUB_FOLDER_SOPORTE  = gh_cfg.get("folder_soporte", "ingreso_soporte")
GITHUB_API_URL_SOPORTE = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{GITHUB_FOLDER_SOPORTE}?ref={GITHUB_BRANCH}"
GITHUB_RAW_BASE_SOPORTE= f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FOLDER_SOPORTE}"

# ==============================================================================
# 2. CONSTANTES GLOBALES
# ==============================================================================
ANIO_BASE_ESTRICTO: int       = 2026
EXCEL_EPOCH_START: pd.Timestamp = pd.Timestamp("1899-12-30")
NOMBRE_SISTEMA: str           = "TOTALPLAY / OPERACIONES — REGIÓN NORTE LA BAJA"
VERSION_SISTEMA: str          = "15.0.0-MASTER"

# Tipos elegibles para denominador de efectividad y para el cálculo de productividad
TIPOS_ELEGIBLES_EFECTIVIDAD = frozenset(["INSTALACION", "INSTALACIÓN", "SOPORTE", "CAMBIO DE DOMICILIO", "CAMBIO DE EQUIPO"])

# Pólizas válidas para el sistema (generación "25" = planta externa: excluida en pipeline)
POLIZAS_VALIDAS: frozenset = frozenset(["D1","D6","E3","M3","M4","MT","R3"])
# Pólizas excluidas por DEFAULT en filtros UI (el usuario puede activarlas manualmente)
# La vista operativa parte de todas las pólizas, salvo MTTO PI.  R3 se conserva
# porque también forma parte del volumen real cuando no se excluye expresamente.
POLIZAS_DEFAULT_EXCLUIDAS: frozenset = frozenset(["MT"])

MAPEO_POLIZAS: Dict[str, str] = {
    "R3": "RECOLECCIÓN",
    "E3": "PÓLIZA 3",
    "M3": "MULTIDISTRITO",
    "M4": "MULTIDISTRITO FLOTANTE",
    "MT": "MTTO PI",
    "D1": "DESTAJO",
    "D6": "DESTAJO 6",
}

DESCRIPCION_POLIZAS: Dict[str, str] = {
    "R3": "Póliza de Recolección de Equipos y Terminado de Operaciones en Campo",
    "E3": "Póliza Estándar Tipo 3 para Instalaciones y Mantenimiento Correctivo",
    "M3": "Póliza Multi-Distrito Operativa asignada a Zonas Urbanas de Alta Densidad",
    "M4": "Póliza Multi-Distrito Flotante para Cuadrillas de Respaldo Inter-Zona",
    "MT": "Póliza de Mantenimiento PI separada del cálculo estándar de volumen",
    "D1": "Póliza por Destajo asignada a Cuadrillas Contratistas Externas",
    "D6": "Póliza por Destajo Tipo 6 para Cuadrillas Contratistas Especializadas",
}

# Proveedores excluidos permanentemente del sistema (ruido / planta ajena)
PROVEEDORES_EXCLUIDOS: frozenset = frozenset([
    "TOTALBOX","TOTALPLAY","ITAI",
    # Variantes con sufijos numéricos (ej. "ITAI 3", "TOTALBOX 2") se detectan por contains
])
PREFIJOS_PROVEEDOR_EXCLUIDO: tuple = ("TOTALBOX","TOTALPLAY","ITAI")

# Tipos de evento NO válidos cuando el cierre viene de póliza generación "25" (planta externa)
TIPOS_EXCLUIDOS_POLIZA25: frozenset = frozenset([
    "CIERRE","DETENCIONES","ETIQUETADO","GASA","POSTE",
    "RED NUEVA","RUTA","TEN GIGA",
])

# Todos los tipos elegibles para conteo en el sistema (incluyendo los que homologan a INSTALACION)
TIPOS_VALIDOS_SISTEMA: frozenset = frozenset([
    "ADDON WIFI EXTENDER","ADDONS","CAMBIO DE DOMICILIO","CAMBIO DE EQUIPO",
    "CAMBIO DE PLAN","EMPRESARIAL","FACTIBILIDAD","HALLAZGO EMPRESARIAL",
    "INSTALACION","INSTALACIÓN",
    "MANTENIMIENTO MAYOR","MANTENIMIENTO MENOR","MANTENIMIENTO PREVENTIVO",
    "RECOLECCION EMPRESARIAL","RECOLECCIÓN EMPRESARIAL",
    "RECOLECCION PI","RECOLECCIÓN PI",
    "SOPORTE",
    # Los siguientes solo para pólizas válidas (no-25)
    "CIERRE","DETENCIONES","ETIQUETADO","GASA","POSTE","RED NUEVA","RUTA","TEN GIGA",
])

# Catálogo canónico de homologación de tipos.
# Clave = valor tal como puede llegar del CSV (incluyendo variantes con
# caracteres corruptos por encoding). Valor = forma canónica del sistema.
# Catálogo de homologación de tipos — se aplica sobre str.upper().strip()
# Por eso todas las claves están en mayúsculas.
# El bloque de contains posterior cubre variantes residuales no listadas.
HOMOLOGACION_TIPOS: Dict[str, str] = {
    "SOPORTE":                      "SOPORTE",
    "EMPRESARIAL":                  "EMPRESARIAL",
    "CAMBIO DE EQUIPO":             "CAMBIO DE EQUIPO",
    "CAMBIO DE DOMICILIO":          "CAMBIO DE DOMICILIO",
    "CAMBIO DE PLAN":               "CAMBIO DE PLAN",
    "ADDONS":                       "ADDONS",
    "ADDON WIFI EXTENDER":          "ADDON WIFI EXTENDER",
    "MANTENIMIENTO MENOR":          "MANTENIMIENTO MENOR",
    "MANTENIMIENTO MAYOR":          "MANTENIMIENTO MAYOR",
    "MANTENIMIENTO PREVENTIVO":     "MANTENIMIENTO PREVENTIVO",
    "HALLAZGO EMPRESARIAL":         "HALLAZGO EMPRESARIAL",
    "FACTIBILIDAD":                 "FACTIBILIDAD",
    "CIERRE":                       "CIERRE",
    "GASA":                         "GASA",
    "ETIQUETADO":                   "ETIQUETADO",
    "POSTE":                        "POSTE",
    "DETENCIONES":                  "DETENCIONES",
    "RUTA":                         "RUTA",
    "TEN GIGA":                     "TEN GIGA",
    "RED NUEVA":                    "RED NUEVA",
    # Instalación — todas las variantes posibles de encoding
    "INSTALACION":                  "INSTALACION",
    "INSTALACIÓN":                  "INSTALACION",
    "INSTALACIÃƒâ€šN": "INSTALACION",
    "INSTALACI‚Ã‚Ã¶N": "INSTALACION",
    "INSTALACI‚Äö√†√∂‚Äö√¢‚Ä¢N": "INSTALACION",
    # Recolección PI
    "RECOLECCION PI":               "RECOLECCION PI",
    "RECOLECCIÓN PI":               "RECOLECCION PI",
    "RECOLECCIÃƒâ€šN PI": "RECOLECCION PI",
    "RECOLECCI‚Äö√†√∂‚Äö√¢‚Ä¢N PI": "RECOLECCION PI",
    # Recolección Empresarial
    "RECOLECCION EMPRESARIAL":      "RECOLECCION EMPRESARIAL",
    "RECOLECCIÓN EMPRESARIAL":      "RECOLECCION EMPRESARIAL",
    "RECOLECCIÃƒâ€šN EMPRESARIAL": "RECOLECCION EMPRESARIAL",
    "RECOLECCI‚Äö√†√∂‚Äö√¢‚Ä¢N EMPRESARIAL": "RECOLECCION EMPRESARIAL",
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
LISTA_ALIAS_LAT:      List[str] = ["latitud","latitude","lat","coordenada_y","coord_lat","y_coord"]
LISTA_ALIAS_LON:      List[str] = ["longitud","longitude","lon","lng","coordenada_x","coord_lon","x_coord"]

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

# Fragmentos que identifican cada tipo elegible de forma robusta
# (resisten variantes con encoding corrupto porque buscan subcadenas cortas)
_FRAGS_SOPORTE     = ("SOPORTE",)
_FRAGS_ANTECEDENTE = ("INSTALA", "SOPORTE", "CAMBIO DE DOMICILIO", "CAMBIO DE EQUIPO")


def _tipo_es_soporte(tipo_str: str) -> bool:
    if pd.isna(tipo_str):
        return False
    t = str(tipo_str).strip().upper()
    return any(f in t for f in _FRAGS_SOPORTE)


def _tipo_es_antecedente_valido(tipo_str: str) -> bool:
    """
    Devuelve True si el tipo del evento previo califica como antecedente.
    Usa contains robusto (fragmentos cortos) en vez de comparación exacta,
    para resistir variantes con encoding corrupto (ej: Instalaci‚àö‚â•n).
    Elegibles: INSTALACION (cualquier variante), SOPORTE, CAMBIO DE DOMICILIO,
               CAMBIO DE EQUIPO.
    """
    if pd.isna(tipo_str):
        return False
    t = str(tipo_str).strip().upper()
    return any(f in t for f in _FRAGS_ANTECEDENTE)


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
    Motor de reincidencias — v2 corregido.

    Reglas de negocio exactas:

    1. ORDEN: cronológico por (Num_Semana ASC, posición de fila ASC).
       Sin dependencia de fecha de cierre para ordenar.

    2. ES_REINCIDENCIA = SI cuando:
       a) El evento actual es de tipo SOPORTE.
       b) Existe algún antecedente previo en la misma cuenta cuyo tipo sea
          elegible: INSTALACION, SOPORTE, CAMBIO DE DOMICILIO, CAMBIO DE EQUIPO.
       c) La brecha entre la FECHA DE CIERRE del antecedente y la FECHA DE
          CREACIÓN del Soporte actual es > 0 y <= 60 días.
          (La instalación "falla" días después de cerrarse, no de crearse.)
       d) Si no hay fecha de cierre disponible, se usa fecha de creación como
          aproximación y la brecha máxima pasa a ser 8 semanas.

    3. CONTEO_PREVIO_8_SEM: número acumulado de reincidencias de esa cuenta
       ANTERIORES a la posición actual (incluyendo eventos no reincidentes).
       Ejemplo cuenta 142838878:
         OT 34165545 Instalación → conteo=0 (no es reincidencia)
         OT 35638804 Soporte     → conteo=1 (1ª reincidencia; brecha 52 días de instalación)
         OT 37420992 Cambio Dom  → conteo=2 (no es reincidencia pero acumula 2)
         OT 37805036 Soporte     → conteo=3 (2ª reincidencia; brecha del cambio domicilio)

    4. TIPO_2: si causa del antecedente es N/A → usar Tipo_Orden del antecedente.

    5. ID primario del técnico: código de usuario (antes del " | ").
    """
    df = df.copy()
    for col, val in [
        ("ES_REINCIDENCIA",           "NO"),
        ("CONTEO_PREVIO_8_SEM",       0),
        ("Usuario_Origen_Reincidencia","N/A"),
        ("Empresa_Origen_Reincidencia","N/A"),
        ("Semana_Origen_Reincidencia", np.nan),
        ("Causa_Origen",               "N/A"),
        ("TIPO_2",                     "N/A"),
        ("Falla_Nueva",                "N/A"),
        ("ES_CASO_ESPECIAL",           "NO"),
    ]:
        df[col] = val

    if df.empty:
        return df

    cols        = list(df.columns)
    col_tech    = detectar_columna_por_patrones(cols, ["usuario_tecnico","tecnico","tech","usuario","atendio","nombre_tecnico"]) or "Usuario_Tecnico"
    col_semana  = detectar_columna_por_patrones(cols, ["num_semana_archivo","semana","sem"]) or "Num_Semana_Archivo"
    col_causa   = detectar_columna_por_patrones(cols, LISTA_ALIAS_CAUSA) or "Tipo_Orden"
    col_falla   = detectar_columna_por_patrones(cols, LISTA_ALIAS_FALLA) or "Tipo_Orden"
    col_empresa = detectar_columna_por_patrones(cols, LISTA_ALIAS_PROVEEDOR) or "Empresa"
    col_tipo    = "Tipo_Orden" if "Tipo_Orden" in cols else col_causa

    COL_PARSED  = "_datetime_parsed"   # fecha de creación parseada
    COL_TERMINO = "_datetime_termino"  # fecha de cierre parseada
    tiene_fechas_creacion = COL_PARSED  in df.columns
    tiene_fechas_cierre   = COL_TERMINO in df.columns

    df["_IDX_ORIG"] = range(len(df))
    df["_SEM_TEMP"] = pd.to_numeric(df[col_semana], errors="coerce").fillna(0).astype(int)

    mask_cta = df["Cuenta_Cliente"].notna() & (
        ~df["Cuenta_Cliente"].astype(str).str.upper().isin(["SIN_CTA","SIN_FOLIO","NAN","NONE",""])
    )
    df_valid = df[mask_cta].sort_values(["Cuenta_Cliente","_SEM_TEMP","_IDX_ORIG"]).copy()

    # Resultados indexados por FOLIO_KEY
    resultados: Dict[str, dict] = {}

    for cuenta, g in df_valid.groupby("Cuenta_Cliente"):
        registros  = g.to_dict("records")
        n          = len(registros)
        conteo_acc = 0   # reincidencias acumuladas hasta el evento i

        for i in range(n):
            reg_act   = registros[i]
            key_act   = reg_act.get("FOLIO_KEY")
            tipo_act  = str(reg_act.get(col_tipo, "")).strip().upper()
            es_soporte = _tipo_es_soporte(tipo_act)

            # Guardar el conteo acumulado ANTES de procesar este evento
            resultados[key_act] = {
                "CONTEO": conteo_acc,
                "ES_REIN": "NO",
                "Usuario_Origen":  "N/A",
                "Empresa_Origen":  "N/A",
                "Semana_Origen":   np.nan,
                "Causa_Origen":    "N/A",
                "TIPO_2":          "N/A",
                "Falla_Nueva":     "N/A",
            }

            if not es_soporte or i == 0:
                continue

            # Buscar antecedente elegible más reciente hacia atrás
            reg_prev = None
            for j in range(i - 1, -1, -1):
                t_prev = str(registros[j].get(col_tipo, "")).strip().upper()
                if _tipo_es_antecedente_valido(t_prev):
                    reg_prev = registros[j]
                    break

            if reg_prev is None:
                continue

            # -------------------------------------------------------
            # Brecha: fecha_cierre_antecedente → fecha_creacion_actual
            # -------------------------------------------------------
            es_rein = False
            semana_origen = reg_prev.get("_SEM_TEMP", np.nan)

            if tiene_fechas_creacion and tiene_fechas_cierre:
                f_cierre_prev = reg_prev.get(COL_TERMINO)   # cierre del antecedente
                f_crear_act   = reg_act.get(COL_PARSED)     # creación del soporte actual
                if pd.notna(f_cierre_prev) and pd.notna(f_crear_act):
                    delta_d = (pd.Timestamp(f_crear_act) - pd.Timestamp(f_cierre_prev)).days
                    es_rein = (0 < delta_d <= 60)
                elif pd.notna(reg_prev.get(COL_PARSED)) and pd.notna(f_crear_act):
                    # Fallback: usar fecha creación del antecedente
                    delta_d = (pd.Timestamp(f_crear_act) - pd.Timestamp(reg_prev[COL_PARSED])).days
                    es_rein = (0 < delta_d <= 60)
                else:
                    # Sin fechas: validar por semana (<= 8)
                    if pd.notna(semana_origen):
                        brecha_sem = int(reg_act.get("_SEM_TEMP", 0)) - int(semana_origen)
                        es_rein = (0 < brecha_sem <= 8)
            else:
                # Sin columnas de fecha: validar solo por semana
                if pd.notna(semana_origen):
                    brecha_sem = int(reg_act.get("_SEM_TEMP", 0)) - int(semana_origen)
                    es_rein = (0 < brecha_sem <= 8)

            if not es_rein:
                continue

            # ---- Es reincidencia ----
            conteo_acc += 1   # incrementar ANTES de guardar en este evento

            tech_prev = str(reg_prev.get(col_tech, "SIN ESPECIFICAR"))
            if tech_prev.upper() in ["NAN","NONE","","N/A","NULL"]:
                tech_prev = "SIN ESPECIFICAR"
            emp_prev = str(reg_prev.get(col_empresa, "SIN EMPRESA"))
            if emp_prev.upper() in ["NAN","NONE","","N/A","NULL"]:
                emp_prev = "SIN EMPRESA"

            causa_raw = reg_prev.get(col_causa)
            tipo_raw  = reg_prev.get(col_tipo)

            resultados[key_act].update({
                "CONTEO":         conteo_acc,
                "ES_REIN":        "SI",
                "Usuario_Origen": tech_prev,
                "Empresa_Origen": emp_prev,
                "Semana_Origen":  semana_origen,
                "Causa_Origen":   str(causa_raw) if pd.notna(causa_raw) else "N/A",
                "TIPO_2":         obtener_valor_tipo2(causa_raw, tipo_raw),
                "Falla_Nueva":    str(reg_act.get(col_falla, "N/A")),
            })

    # Asignar resultados al DataFrame original
    if resultados:
        folio_series = df["FOLIO_KEY"]
        df["CONTEO_PREVIO_8_SEM"]        = folio_series.map(lambda k: resultados.get(k,{}).get("CONTEO", 0))
        df["ES_REINCIDENCIA"]            = folio_series.map(lambda k: resultados.get(k,{}).get("ES_REIN","NO"))
        mask_r = df["ES_REINCIDENCIA"] == "SI"
        if mask_r.any():
            for dest, campo in [
                ("Usuario_Origen_Reincidencia", "Usuario_Origen"),
                ("Empresa_Origen_Reincidencia", "Empresa_Origen"),
                ("Causa_Origen",                "Causa_Origen"),
                ("TIPO_2",                      "TIPO_2"),
                ("Falla_Nueva",                 "Falla_Nueva"),
            ]:
                df.loc[mask_r, dest] = folio_series[mask_r].map(
                    lambda k, c=campo: resultados.get(k, {}).get(c, "N/A")
                )
            df.loc[mask_r, "Semana_Origen_Reincidencia"] = folio_series[mask_r].map(
                lambda k: resultados.get(k, {}).get("Semana_Origen", np.nan)
            )

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
    c_lat    = get_col(LISTA_ALIAS_LAT)
    c_lon    = get_col(LISTA_ALIAS_LON)
    if c_causa  and c_causa  in cols: df["Causa_Registro"]   = sanit_txt(df[c_causa])
    if c_falla  and c_falla  in cols: df["Falla_Registro"]   = sanit_txt(df[c_falla])
    if c_sol    and c_sol    in cols: df["Solucion_Registro"] = sanit_txt(df[c_sol])
    if c_status and c_status in cols: df["Estatus_Registro"]  = sanit_txt(df[c_status])
    # Coordenadas: convertir a float, valores inválidos → NaN
    if c_lat and c_lat in cols:
        df["LAT"] = pd.to_numeric(df[c_lat].astype(str).str.replace(",",".",regex=False), errors="coerce")
    if c_lon and c_lon in cols:
        df["LON"] = pd.to_numeric(df[c_lon].astype(str).str.replace(",",".",regex=False), errors="coerce")

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

    # ------------------------------------------------------------------
    # FILTRO DE PLANTA EXTERNA (generación "25")
    # Un técnico es "planta externa gen-25" si los caracteres 3-4 del
    # código de usuario son "25" (ej: ITA25TIJT2981, POL25MEXT0108).
    # Para estos usuarios:
    #   • Se elimina el registro SI el tipo de evento está en
    #     TIPOS_EXCLUIDOS_POLIZA25 (Cierre, Detenciones, Gasa, etc.).
    #   • Si el tipo es uno de los elegibles, el registro SE MANTIENE.
    # ------------------------------------------------------------------
    if c_pol and c_pol in df.columns:
        gen_cod = df[c_pol].astype(str).str[3:5]
        mask_25 = gen_cod == "25"
        mask_tipo_excluido = df["Tipo_Orden"].str.upper().isin(TIPOS_EXCLUIDOS_POLIZA25)
        # Eliminar: es planta externa Y el tipo es de los excluidos
        df = df[~(mask_25 & mask_tipo_excluido)].copy()
        cols = list(df.columns)  # refrescar después del filtro

    # ------------------------------------------------------------------
    # HOMOLOGACIÓN DE TIPOS DE EVENTO
    # 1. Primero mapa exacto (cubre variantes con encoding corrupto).
    # 2. Luego contains sobre el resultado normalizado a mayúsculas,
    #    para cubrir cualquier variante no listada en el catálogo.
    # ------------------------------------------------------------------
    if "Tipo_Orden" in df.columns:
        # Paso 1: mapa exacto (case-insensitive sobre upper())
        tipo_up = df["Tipo_Orden"].str.upper().str.strip()
        mapa_upper = {k.upper(): v for k, v in HOMOLOGACION_TIPOS.items()}
        df["Tipo_Orden"] = tipo_up.map(mapa_upper).fillna(tipo_up)

        # Paso 2: contains para cubrir variantes residuales no en el mapa
        tipo_up2 = df["Tipo_Orden"].str.upper().str.strip()
        mask_rec_emp = tipo_up2.str.contains("RECOLE", na=False) & (
            tipo_up2.str.contains("EMPRE", na=False)
        )
        mask_rec_pi  = tipo_up2.str.contains("RECOLE", na=False) & ~mask_rec_emp
        mask_inst    = tipo_up2.str.contains("INSTALA", na=False) & ~mask_rec_emp & ~mask_rec_pi
        df.loc[mask_rec_emp, "Tipo_Orden"] = "RECOLECCION EMPRESARIAL"
        df.loc[mask_rec_pi,  "Tipo_Orden"] = "RECOLECCION PI"
        df.loc[mask_inst,    "Tipo_Orden"] = "INSTALACION"

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

    # ------------------------------------------------------------------
    # FILTROS DE CALIDAD (reglas 8-11)
    # Se aplican DESPUÉS de la homologación y ANTES del motor de
    # reincidencias para que el análisis histórico ya use el dataset limpio.
    # ------------------------------------------------------------------

    # Regla 8: Proveedores excluidos permanentemente
    # Cubre variantes como "ITAI 3", "TOTALBOX 2", "TOTALPLAY TIJUANA", etc.
    if "Empresa" in df.columns:
        mask_prov_exc = df["Empresa"].str.upper().str.startswith(PREFIJOS_PROVEEDOR_EXCLUIDO)
        df = df[~mask_prov_exc].copy()

    # Regla 9 & 10: Usuarios y proveedores con UN SOLO evento en todo el dataset
    # (ruido estadístico; no aportan a tendencias ni a productividad real).
    # Se calcula sobre el dataset ya filtrado para no contaminar con los excluidos.
    if "Usuario_Tecnico" in df.columns:
        conteo_usr = df["Usuario_Tecnico"].map(df["Usuario_Tecnico"].value_counts())
        df = df[conteo_usr > 1].copy()

    if "Empresa" in df.columns:
        conteo_emp = df["Empresa"].map(df["Empresa"].value_counts())
        df = df[conteo_emp > 1].copy()

    # Motor de reincidencias sobre el dataset COMPLETO (sin filtros)
    # NOTA: cuando se llama desde _regenerar_parquet_consolidado, las
    # reincidencias se calculan en el consolidado total, no aquí.
    # El parámetro calcular_rein=True mantiene compatibilidad con el fallback.
    df = calcular_reincidencias_vectorizadas(df)
    return df


def transformar_dataset_base(df: pd.DataFrame) -> pd.DataFrame:
    """
    Igual que transformar_dataset_completo pero SIN calcular reincidencias.
    Se usa en la regeneración del parquet para que las reincidencias
    se calculen sobre el consolidado total (histórico + nuevo), no
    solo sobre el archivo recién subido.
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    cols = list(df.columns)

    get_col   = lambda alias: detectar_columna_por_patrones(cols, alias)
    sanit_fol = lambda s: s.apply(sanitizar_folio_identificador)
    sanit_txt = lambda s: s.apply(sanitizar_cadena_texto)

    col_fecha     = get_col(LISTA_ALIAS_CREACION)
    col_fecha_fin = get_col(LISTA_ALIAS_TERMINO)
    df["_datetime_parsed"]  = parsear_columna_fecha_robusta(df[col_fecha]) if col_fecha and col_fecha in cols else pd.NaT
    df["_datetime_termino"] = parsear_columna_fecha_robusta(df[col_fecha_fin]) if col_fecha_fin and col_fecha_fin in cols else pd.NaT
    delta_h = (df["_datetime_termino"] - df["_datetime_parsed"]).dt.total_seconds() / 3600.0
    df["Tiempo_Resolucion_Horas"] = delta_h.where(delta_h >= 0)

    c_os  = get_col(LISTA_ALIAS_ORDEN);    c_cta = get_col(LISTA_ALIAS_CUENTA)
    c_ot  = get_col(LISTA_ALIAS_OT);      c_tipo = get_col(LISTA_ALIAS_TIPO)
    c_usr = get_col(LISTA_ALIAS_USUARIO); c_nom  = get_col(LISTA_ALIAS_NOMBRE)
    c_prov= get_col(LISTA_ALIAS_PROVEEDOR);c_dist= get_col(LISTA_ALIAS_DISTRITO)
    c_clus= get_col(LISTA_ALIAS_CLUSTER)
    c_causa  = get_col(LISTA_ALIAS_CAUSA);  c_falla = get_col(LISTA_ALIAS_FALLA)
    c_sol    = get_col(LISTA_ALIAS_SOLUCION); c_status = get_col(LISTA_ALIAS_ESTATUS)
    c_lat    = get_col(LISTA_ALIAS_LAT);   c_lon = get_col(LISTA_ALIAS_LON)

    if c_causa  and c_causa  in cols: df["Causa_Registro"]    = sanit_txt(df[c_causa])
    if c_falla  and c_falla  in cols: df["Falla_Registro"]    = sanit_txt(df[c_falla])
    if c_sol    and c_sol    in cols: df["Solucion_Registro"]  = sanit_txt(df[c_sol])
    if c_status and c_status in cols: df["Estatus_Registro"]   = sanit_txt(df[c_status])
    if c_lat    and c_lat    in cols: df["LAT"] = pd.to_numeric(df[c_lat].astype(str).str.replace(",",".",regex=False), errors="coerce")
    if c_lon    and c_lon    in cols: df["LON"] = pd.to_numeric(df[c_lon].astype(str).str.replace(",",".",regex=False), errors="coerce")

    s_os   = sanit_fol(df[c_os])   if c_os   else pd.Series("SIN_OS",  index=df.index)
    s_cta  = sanit_fol(df[c_cta])  if c_cta  else pd.Series("SIN_CTA", index=df.index)
    s_ot   = sanit_fol(df[c_ot])   if c_ot   else pd.Series("SIN_OT",  index=df.index)
    s_tipo = sanit_txt(df[c_tipo])  if c_tipo else pd.Series("EVENTO GENERAL", index=df.index)

    df["FOLIO_KEY"]      = s_os.astype(str)+"_"+s_cta.astype(str)+"_"+s_ot.astype(str)+"_"+s_tipo.astype(str)
    df["Cuenta_Cliente"] = s_cta.astype(str)

    s_u = sanit_txt(df[c_usr]) if c_usr else pd.Series("SIN ESPECIFICAR", index=df.index)
    s_n = sanit_txt(df[c_nom]) if c_nom else pd.Series("SIN ESPECIFICAR", index=df.index)
    df["Usuario_Tecnico"] = np.where(
        (s_u != "SIN ESPECIFICAR") & (s_n != "SIN ESPECIFICAR"), s_u+" | "+s_n,
        np.where(s_n != "SIN ESPECIFICAR", s_n, s_u)
    )
    df["Empresa"]      = sanit_txt(df[c_prov]) if c_prov else "SIN PROVEEDOR"
    df["Distrito"]     = sanit_txt(df[c_dist]) if c_dist else "DISTRITO GENERAL"
    df["Tipo_Orden"]   = s_tipo
    df["Cluster_Raw"]  = sanit_txt(df[c_clus]) if c_clus else "SIN CLUSTER"
    df["Cluster_Base"] = normalizar_clusters_vectorizado(df["Cluster_Raw"])

    c_pol = c_usr or c_nom
    if c_pol and c_pol in cols:
        sub_cods = df[c_pol].astype(str).str[3:5]
        df["Codigo_Poliza"] = np.where(sub_cods.isin(MAPEO_POLIZAS), sub_cods, "")
        df["Nombre_Poliza"] = df["Codigo_Poliza"].map(MAPEO_POLIZAS).fillna("NO VALIDO")
    else:
        df["Codigo_Poliza"] = ""; df["Nombre_Poliza"] = "NO VALIDO"

    if c_pol and c_pol in cols:
        gen_cod = df[c_pol].astype(str).str[3:5]
        mask_25 = gen_cod == "25"
        mask_tipo_excluido = df["Tipo_Orden"].str.upper().isin(TIPOS_EXCLUIDOS_POLIZA25)
        df = df[~(mask_25 & mask_tipo_excluido)].copy()
        cols = list(df.columns)

    if "Tipo_Orden" in df.columns:
        tipo_up = df["Tipo_Orden"].str.upper().str.strip()
        mapa_upper = {k.upper(): v for k, v in HOMOLOGACION_TIPOS.items()}
        df["Tipo_Orden"] = tipo_up.map(mapa_upper).fillna(tipo_up)
        tipo_up2 = df["Tipo_Orden"].str.upper().str.strip()
        mask_rec_emp = tipo_up2.str.contains("RECOLE", na=False) & tipo_up2.str.contains("EMPRE", na=False)
        mask_rec_pi  = tipo_up2.str.contains("RECOLE", na=False) & ~mask_rec_emp
        mask_inst    = tipo_up2.str.contains("INSTALA", na=False) & ~mask_rec_emp & ~mask_rec_pi
        df.loc[mask_rec_emp, "Tipo_Orden"] = "RECOLECCION EMPRESARIAL"
        df.loc[mask_rec_pi,  "Tipo_Orden"] = "RECOLECCION PI"
        df.loc[mask_inst,    "Tipo_Orden"] = "INSTALACION"

    if "Empresa" in df.columns:
        mask_prov_exc = df["Empresa"].str.upper().str.startswith(PREFIJOS_PROVEEDOR_EXCLUIDO)
        df = df[~mask_prov_exc].copy()

    if "Usuario_Tecnico" in df.columns:
        conteo_usr = df["Usuario_Tecnico"].map(df["Usuario_Tecnico"].value_counts())
        df = df[conteo_usr > 1].copy()
    if "Empresa" in df.columns:
        conteo_emp = df["Empresa"].map(df["Empresa"].value_counts())
        df = df[conteo_emp > 1].copy()

    sem_arch = df["Archivo_Origen"].apply(extraer_numero_semana_archivo) if "Archivo_Origen" in df.columns else pd.Series(None, index=df.index)
    sem_iso  = pd.to_numeric(df["_datetime_parsed"].dt.isocalendar().week, errors="coerce").fillna(0).astype(int)
    df["Num_Semana_Archivo"] = np.where(
        pd.to_numeric(sem_arch, errors="coerce").fillna(0) > 0,
        pd.to_numeric(sem_arch, errors="coerce").fillna(0).astype(int), sem_iso
    )
    df["AÑO_DIM"]    = str(ANIO_BASE_ESTRICTO)
    df["SEMANA_DIM"] = df["Num_Semana_Archivo"].apply(lambda x: f"Sem {int(x)}" if x > 0 else "SIN_FECHA")
    df["MES_DIM"]    = df["_datetime_parsed"].dt.month.fillna(1).astype(int).map(MAPEO_MESES_TEXTO).fillna("Enero")
    dates_valid = df["_datetime_parsed"].dropna()
    df["FECHA_TRUNCADA"] = f"01.01.{ANIO_BASE_ESTRICTO}"
    if not dates_valid.empty:
        df.loc[dates_valid.index, "FECHA_TRUNCADA"] = dates_valid.dt.strftime(f"%d.%m.{ANIO_BASE_ESTRICTO}")

    return df


@st.cache_data(ttl=900, show_spinner="Cargando dataset consolidado...", max_entries=1)
def ejecutar_pipeline_ingestion_datos() -> pd.DataFrame:
    """
    Fuente exclusiva del dashboard: datos_consolidados.parquet.

    Los CSV son únicamente insumos de respaldo y se consolidan fuera de
    Streamlit. Nunca se descargan durante la apertura del dashboard: si el
    Parquet no está disponible o no es válido, se devuelve vacío para mostrar
    un error inmediato en lugar de activar una carga lenta por archivos.
    """
    cols_base = ["FOLIO_KEY","Cuenta_Cliente","Usuario_Tecnico","Empresa",
                 "Distrito","Tipo_Orden","Cluster_Base","Nombre_Poliza",
                 "Num_Semana_Archivo","SEMANA_DIM","FECHA_TRUNCADA"]

    try:
        resp = requests.get(f"{GITHUB_RAW_BASE}/datos_consolidados.parquet", headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            df = pd.read_parquet(io.BytesIO(resp.content))
            if not df.empty and "MES_DIM" in df.columns:
                return _homologar_tipos_df(df)
        logger.error("Parquet no disponible o incompleto (HTTP %s).", resp.status_code)
    except Exception as e:
        logger.warning(f"No se pudo descargar Parquet consolidado: {e}")
    return pd.DataFrame(columns=cols_base)


# ==============================================================================
# PIPELINE INGRESO SOPORTE (estructura preparada — carpeta ingreso_soporte/)
# ==============================================================================
# Columnas esperadas en los archivos de ingreso_soporte/ (a confirmar con el
# equipo cuando el archivo esté disponible).  Las claves de cruce con
# datos_semanales/ son: OT, OS y/o Cuenta.  Al integrarse, se generará un
# único parquet consolidado con la información combinada.

COLUMNAS_ESPERADAS_INGRESO_SOPORTE: List[str] = [
    # Claves de cruce (deben coincidir con datos_semanales)
    "OT", "OS", "Cuenta",
    # Columnas propias del ingreso de soporte (pendiente de confirmar)
    # "Tipo_Ingreso", "Fecha_Ingreso", "Tecnico_Ingreso", ...
]


@st.cache_data(ttl=3600, show_spinner=False, max_entries=1)
def cargar_ingreso_soporte() -> pd.DataFrame:
    """
    Descarga y procesa los archivos de la carpeta ingreso_soporte/ en GitHub.
    Retorna un DataFrame listo para cruzarse con datos_semanales.
    Si la carpeta no existe o está vacía, retorna DataFrame vacío sin error.

    TODO: cuando el archivo esté disponible, definir aquí:
      - Mapeo de columnas al esquema común (FOLIO_KEY, Cuenta_Cliente, Tipo_Orden…)
      - Reglas de homologación propias del ingreso de soporte
      - Cruce con datos_semanales por OT / OS / Cuenta
    """
    try:
        resp = requests.get(GITHUB_API_URL_SOPORTE, headers=HEADERS, timeout=10)
        if resp.status_code == 404:
            return pd.DataFrame()   # carpeta aún no existe
        if resp.status_code != 200:
            logger.warning(f"ingreso_soporte: HTTP {resp.status_code}")
            return pd.DataFrame()
        archivos = [
            f["name"] for f in resp.json()
            if isinstance(f, dict) and f["name"].endswith((".csv", ".parquet"))
        ]
        if not archivos:
            return pd.DataFrame()

        coleccion = []
        with ThreadPoolExecutor(max_workers=10) as ex:
            def _dl(nombre):
                try:
                    r = requests.get(f"{GITHUB_RAW_BASE_SOPORTE}/{nombre}",
                                     headers=HEADERS, timeout=15)
                    if r.status_code == 200:
                        bdata = io.BytesIO(r.content)
                        df_t  = (pd.read_parquet(bdata) if nombre.endswith(".parquet")
                                 else pd.read_csv(bdata, dtype=str, low_memory=False,
                                                  encoding="utf-8", on_bad_lines="skip"))
                        df_t["Archivo_Soporte"] = nombre
                        return df_t
                except Exception as e:
                    logger.error(f"ingreso_soporte/{nombre}: {e}")
                return None
            coleccion = [d for d in ex.map(_dl, archivos) if d is not None]

        if not coleccion:
            return pd.DataFrame()

        df_soporte = pd.concat(coleccion, ignore_index=True)
        # TODO: aplicar transformaciones específicas de ingreso_soporte aquí
        return df_soporte

    except Exception as e:
        logger.error(f"Error cargando ingreso_soporte: {e}")
        return pd.DataFrame()


def _homologar_tipos_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica homologación de Tipo_Orden sobre cualquier DataFrame ya cargado.
    Se llama tanto sobre el parquet consolidado (ruta rápida) como sobre
    el resultado del fallback, para garantizar que el motor de reincidencias
    siempre reciba tipos canónicos.
    """
    if "Tipo_Orden" not in df.columns:
        return df
    tipo_up = df["Tipo_Orden"].str.upper().str.strip()
    mapa_upper = {k.upper(): v for k, v in HOMOLOGACION_TIPOS.items()}
    df = df.copy()
    df["Tipo_Orden"] = tipo_up.map(mapa_upper).fillna(tipo_up)
    tipo_up2 = df["Tipo_Orden"].str.upper().str.strip()
    mask_rec_emp = tipo_up2.str.contains("RECOLE", na=False) & tipo_up2.str.contains("EMPRE", na=False)
    mask_rec_pi  = tipo_up2.str.contains("RECOLE", na=False) & ~mask_rec_emp
    mask_inst    = tipo_up2.str.contains("INSTALA", na=False) & ~mask_rec_emp & ~mask_rec_pi
    df.loc[mask_rec_emp, "Tipo_Orden"] = "RECOLECCION EMPRESARIAL"
    df.loc[mask_rec_pi,  "Tipo_Orden"] = "RECOLECCION PI"
    df.loc[mask_inst,    "Tipo_Orden"] = "INSTALACION"
    return df


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


def calcular_dias_cuadrilla_ponderados(
    df: pd.DataFrame,
    col_usuario: str = "Usuario_Tecnico",
    col_fecha: str = "FECHA_TRUNCADA",
    dias_activos: Optional[int] = None,
) -> int:
    """
    Días-Cuadrilla = suma de días reales trabajados por cada cuadrilla.

    Al filtrar un Distrito, Empresa o Usuario se recalcula sobre ese mismo
    subconjunto. Ejemplo: un usuario con órdenes en 6 días aporta 6, aunque la
    semana tenga 7 días calendario. En una semana completa, si 62 cuadrillas
    trabajaron los 7 días, el resultado es 62 × 7 = 434.
    """
    if df.empty or col_usuario not in df.columns:
        return 0

    jornadas = df[[col_usuario]].copy()
    # El parquet conserva la fecha parseada. Se usa antes que FECHA_TRUNCADA
    # para evitar depender del texto mostrado en tablas o gráficas.
    if "_datetime_parsed" in df.columns:
        fechas = pd.to_datetime(df["_datetime_parsed"], errors="coerce")
    else:
        fechas = pd.Series(pd.NaT, index=df.index)
    if fechas.isna().all() and col_fecha in df.columns:
        fechas = pd.to_datetime(df[col_fecha], format="%d.%m.%Y", errors="coerce")
        if fechas.isna().all():
            fechas = pd.to_datetime(df[col_fecha], dayfirst=True, errors="coerce")
    if fechas.isna().all():
        return 0
    jornadas["_fecha_operativa"] = fechas.dt.normalize()
    jornadas[col_usuario] = jornadas[col_usuario].astype(str).str.strip()
    jornadas = jornadas[
        ~jornadas[col_usuario].str.upper().isin(["", "NAN", "NONE", "SIN ESPECIFICAR"])
        & jornadas["_fecha_operativa"].notna()
        & (jornadas["_fecha_operativa"] != pd.Timestamp(f"{ANIO_BASE_ESTRICTO}-01-01"))
    ]
    if jornadas.empty:
        return 0
    return int(jornadas.drop_duplicates([col_usuario, "_fecha_operativa"]).shape[0])


def contar_cuadrillas_activas(df: pd.DataFrame, col_usuario: str = "Usuario_Tecnico") -> int:
    """Cuenta sólo técnicos/cuadrillas identificables, igual que días-cuadrilla."""
    if df.empty or col_usuario not in df.columns:
        return 0
    tecnicos = df[col_usuario].astype(str).str.strip()
    tecnicos = tecnicos[~tecnicos.str.upper().isin(["", "NAN", "NONE", "SIN ESPECIFICAR"])]
    return int(tecnicos.nunique())


def contar_eventos_completados(df: pd.DataFrame) -> int:
    """Cuenta OT/eventos por folio único sobre el subconjunto ya filtrado."""
    if df.empty:
        return 0
    if "FOLIO_KEY" in df.columns:
        return int(df["FOLIO_KEY"].dropna().nunique())
    return int(len(df))


def calcular_capacidad_cuadrilla(
    df: pd.DataFrame,
    dimension: str,
    semanas_filtradas: Optional[List[str]] = None,
    meses_filtrados: Optional[List[str]] = None,
) -> int:
    """Devuelve días-cuadrilla reales del resultado ya filtrado.

    ``dimension`` y los filtros temporales se mantienen en la firma para que
    todas las vistas puedan llamarlo igual; las fechas reales del subconjunto
    determinan el resultado, por lo que no se inventan jornadas no trabajadas.
    """
    return calcular_dias_cuadrilla_ponderados(df)


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
    dimension: str = "SEMANA_DIM",
    semanas_filtradas: Optional[List[str]] = None,
    meses_filtrados: Optional[List[str]] = None,
) -> MetricasResumenKPI:
    """
    KPIs dinámicos: responden a los filtros activos de UI (df_folios).
    df_raw_completo se reserva para futuras comparativas; el cálculo
    de productividad opera sobre el subconjunto filtrado.

    POR DÍA  : Eventos / Técnicos
    POR SEMANA: Eventos / (Cuadrillas activas × 7 días)
    POR MES  : Eventos / (Técnicos × días del mes calendario)
    POR AÑO  : Eventos / (Técnicos × días del año)

    Tarjeta "Dias Cuadrilla" muestra:
      - SEMANA: cuadrillas activas × 7 días por cada semana seleccionada
      - MES   : días del mes calendario
      - DÍA   : 1
      - AÑO   : 365/366
    """
    # Numerador único para todo el módulo: eventos/folios completados únicos
    # después de Semana, Distrito, Empresa, Póliza y Técnico.
    total_eventos  = contar_eventos_completados(df_folios)
    total_usuarios = contar_cuadrillas_activas(df_folios) if total_eventos > 0 else 0

    if total_eventos == 0:
        return MetricasResumenKPI(
            total_eventos=0, total_usuarios=0,
            dias_operativos=0, productividad_diaria=0.0,
            eventos_r3=0, eventos_mt=0
        )

    dias_cuad = calcular_capacidad_cuadrilla(
        df_folios, dimension, semanas_filtradas, meses_filtrados
    )
    prod_base = round(total_eventos / dias_cuad, 2) if dias_cuad > 0 else 0.0

    conteo_r3 = (df_folios["Codigo_Poliza"] == "R3").sum() if "Codigo_Poliza" in df_folios.columns else 0
    conteo_mt = (df_folios["Codigo_Poliza"] == "MT").sum() if "Codigo_Poliza" in df_folios.columns else 0

    return MetricasResumenKPI(
        total_eventos=total_eventos, total_usuarios=total_usuarios,
        dias_operativos=dias_cuad, productividad_diaria=prod_base,
        eventos_r3=int(conteo_r3), eventos_mt=int(conteo_mt)
    )


def _num_sem(val) -> int:
    try:
        return int(float(str(val).replace("Sem","").replace("Semana","").strip()))
    except (ValueError, TypeError):
        return 999


def acotar_fechas_a_semanas_seleccionadas(
    df: pd.DataFrame, semanas: List[str]
) -> Tuple[pd.DataFrame, List[str]]:
    """Restringe los registros a la ventana calendario de la semana de archivo.

    El nombre del CSV asigna ``SEMANA_DIM``; sin este paso una semana podía
    contener registros con fechas de otras semanas al cambiar la gráfica a Día.
    La operación considera lunes a domingo, como se maneja en el cierre
    operativo (por ejemplo Sem 38 = 14 a 20 de septiembre de 2026).
    """
    if df.empty or not semanas or "_datetime_parsed" not in df.columns:
        return df, []

    fechas = pd.to_datetime(df["_datetime_parsed"], errors="coerce").dt.normalize()
    mascara = pd.Series(False, index=df.index)
    etiquetas: List[str] = []
    for semana in sorted({_num_sem(s) for s in semanas if _num_sem(s) != 999}):
        inicio = pd.Timestamp(datetime.fromisocalendar(ANIO_BASE_ESTRICTO, semana, 1))
        fin = inicio + pd.Timedelta(days=6)  # domingo de la semana operativa
        mascara |= fechas.between(inicio, fin, inclusive="both")
        etiquetas.append(f"Sem {semana}: {inicio:%d/%m}–{fin:%d/%m}")
    return df.loc[mascara].copy(), etiquetas


def generar_figura_evolucion_temporal(
    df_folios: pd.DataFrame,
    dimension_temporal: str,
    modo_barras: str = "Eventos Completados",
    semanas_filtradas: Optional[List[str]] = None,
    meses_filtrados: Optional[List[str]] = None,
) -> go.Figure:
    """
    Gráfico de barras + línea de productividad con tendencias.
    modo_barras:
      "Eventos Completados"              → Y = total de órdenes por período
      "Cuadrillas Únicas con Actividad"  → Y = técnicos únicos por período
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
    mapa_grp   = dict(tuple(df_t.groupby(dimension_temporal)))
    usar_cuadrillas = (modo_barras == "Cuadrillas Únicas con Actividad")

    eje_x, vals_ev, vals_prod = [], [], []

    for cat in eje_x_base:
        grupo = mapa_grp.get(cat, pd.DataFrame())
        ev = contar_eventos_completados(grupo)
        if ev <= 0:
            continue
        eje_x.append(str(cat))
        u     = max(contar_cuadrillas_activas(grupo), 1)

        # Valor Y de la barra según toggle
        if usar_cuadrillas:
            vals_ev.append(float(u))   # técnicos únicos con actividad
        else:
            vals_ev.append(float(ev))  # total eventos

        dias_cuad = calcular_capacidad_cuadrilla(
            # En la gráfica cada punto se calcula por su propia dimensión:
            # un día = 1 día, una semana = 7 y un mes = sus días calendario.
            grupo, dimension_temporal
        )
        prod_val = round(ev / dias_cuad, 2) if dias_cuad > 0 else 0.0

        vals_prod.append(prod_val)

    if not eje_x:
        return go.Figure().update_layout(title="Sin registros activos en el rango seleccionado")

    fig = go.Figure()
    nombre_barra = "Cuadrillas Únicas" if usar_cuadrillas else "Eventos Completados"
    titulo_y_izq = "Cuadrillas Únicas (técnicos)" if usar_cuadrillas else "OT / Eventos Completados"
    fig.add_trace(go.Bar(
        x=eje_x, y=vals_ev, name=nombre_barra,
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
            title=dict(text=titulo_y_izq, font=dict(color="#000000", size=11)),
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


def _leer_csv_historico_github(ruta: str) -> pd.DataFrame:
    """Lee un CSV del árbol completo del repositorio y conserva su origen."""
    datos = leer_bytes_github(ruta)
    try:
        df = pd.read_csv(io.BytesIO(datos), dtype=str, low_memory=False,
                         encoding="utf-8", on_bad_lines="skip")
    except UnicodeDecodeError:
        df = pd.read_csv(io.BytesIO(datos), dtype=str, low_memory=False,
                         encoding="latin1", on_bad_lines="skip")
    if df.empty:
        raise ValueError(f"El archivo no contiene filas: {ruta}")
    df["Archivo_Origen"] = ruta
    return df


def _regenerar_parquet_consolidado(nombre_csv: str, df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Reconstruye el Parquet desde TODOS los CSV históricos del repositorio.

    Los CSV archivados en ``datos_semanales/archivo/`` también son parte del
    histórico. No se utiliza el Parquet previo como fuente porque éste podría
    estar incompleto; de ese modo una regeneración siempre corrige el historial
    y recalcula las reincidencias sobre la base completa.
    """
    inventario_repositorio_github.clear()
    inventario = inventario_repositorio_github()
    prefijo = f"{GITHUB_FOLDER.strip('/')}/"

    rutas_csv = sorted(
        ruta for ruta, tipo in inventario.items()
        if tipo == "blob"
        and ruta.startswith(prefijo)
        and ruta.lower().endswith(".csv")
    )
    if not rutas_csv:
        raise ValueError("No se encontraron CSV históricos para reconstruir el consolidado.")

    # El CSV recién guardado ya forma parte del árbol. Si GitHub aún no lo
    # devuelve por latencia, se incorpora la copia recibida por la pantalla.
    nombre_actual_en_arbol = any(ruta.rsplit("/", 1)[-1] == nombre_csv for ruta in rutas_csv)
    errores: List[str] = []
    frames: List[pd.DataFrame] = []

    with ThreadPoolExecutor(max_workers=min(12, len(rutas_csv))) as ex:
        futuros = {ex.submit(_leer_csv_historico_github, ruta): ruta for ruta in rutas_csv}
        for futuro, ruta in futuros.items():
            try:
                frames.append(futuro.result())
            except Exception as exc:
                errores.append(f"{ruta}: {exc}")

    # Nunca crear un Parquet parcial: si un histórico no puede leerse, se
    # conserva el consolidado vigente y se informa el archivo problemático.
    if errores:
        raise ValueError("No se reconstruyó el Parquet porque fallaron archivos históricos: " + " | ".join(errores[:5]))

    if not nombre_actual_en_arbol:
        nuevo = df_raw.copy()
        nuevo["Archivo_Origen"] = f"{prefijo}{nombre_csv}"
        frames.append(nuevo)

    historico_crudo = pd.concat(frames, ignore_index=True)
    logger.info("Reconstrucción completa: %s CSV y %s filas crudas", len(frames), len(historico_crudo))

    consolidado_base = transformar_dataset_base(historico_crudo)
    if consolidado_base is None or consolidado_base.empty:
        raise ValueError("La transformación del histórico produjo un dataset vacío.")

    consolidado_final = calcular_reincidencias_vectorizadas(consolidado_base)
    logger.info("Parquet reconstruido: %s filas finales", len(consolidado_final))
    return consolidado_final


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
        # El archivo existe en GitHub aunque el usuario eligió "Crear nuevo".
        # Esto ocurre cuando el inventario cacheado está desactualizado o cuando
        # el usuario confirmó explícitamente la sobrescritura en el formulario.
        # Usamos el SHA para hacer un PUT y sobrescribir el archivo.
        sha = res.json().get("sha")
        # final ya tiene los datos nuevos (df), no concatenamos con el anterior
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
    /* Botones de descarga siempre legibles */
    div[data-testid="stDownloadButton"] > button {{
        background-color: #FFFFFF !important;
        color: {p["azul_marina"]} !important;
        border: 1px solid {p["gris_borde"]} !important;
        border-radius: 7px !important;
        font-family: Arial, sans-serif !important;
        font-weight: 600 !important;
        font-size: 12px !important;
        width: auto !important;
    }}
    /* Métricas con fondo adaptado */
    [data-testid="stMetricValue"] {{
        color: {p["turquesa_cyan"]} !important;
        font-family: Arial, sans-serif !important;
        font-weight: 700 !important;
    }}
    [data-testid="stMetricLabel"] {{
        color: {p["gris_borde"]} !important;
        font-family: Arial, sans-serif !important;
    }}
    [data-testid="stMetricDelta"] {{
        font-family: Arial, sans-serif !important;
    }}
    /* Number inputs con texto legible */
    input[type="number"] {{
        color: #000000 !important;
        background: #FFFFFF !important;
    }}
    /* Sidebar métrica legible */
    [data-testid="stSidebar"] [data-testid="stMetricValue"] {{
        color: {p["turquesa_cyan"]} !important;
    }}
    [data-testid="stSidebar"] [data-testid="stMetricLabel"] {{
        color: #FFFFFF !important;
    }}

    /* === RESPONSIVO MÓVIL === */
    .kpi-grid-4 {{
        display: grid !important;
        grid-template-columns: repeat(4, 1fr) !important;
        gap: 10px !important;
        margin-bottom: 16px !important;
    }}
    @media (max-width: 640px) {{
        .kpi-grid-4 {{ grid-template-columns: repeat(2, 1fr) !important; }}
        .kpi-card-value {{ font-size: 22px !important; }}
        .kpi-card-title {{ font-size: 9px !important; }}
        .kpi-card-subtitle {{ font-size: 9px !important; }}
        .js-plotly-plot .plotly .drag {{ pointer-events: none !important; }}
        .js-plotly-plot {{ touch-action: none !important; }}
        [data-testid="stDataFrame"] {{ overflow-x: auto !important; }}
    }}
    /* Deshabilitar pinch-zoom en gráficos en todos los dispositivos */
    .js-plotly-plot {{ touch-action: pan-y !important; }}
    /* Sidebar colapsado empuja el contenido a ancho completo */
    [data-testid="stSidebar"][aria-expanded="false"] ~ .main {{
        margin-left: 0 !important; width: 100% !important;
    }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


# ==============================================================================
# 10. VISTAS
# ==============================================================================

def renderizar_pestana_polizas_cuadrillas(
    df_folios: pd.DataFrame,
    df_raw: pd.DataFrame,
    dimension_sel: str,
    semanas_filtradas: Optional[List[str]] = None,
    meses_filtrados: Optional[List[str]] = None,
) -> None:
    kpis = extraer_metricas_kpi_totales(
        df_folios, df_raw, dimension_sel, semanas_filtradas, meses_filtrados
    )

    sub_tab1, sub_tab2, sub_tab3 = st.tabs([
        "Evolución y Productividad",
        "Desglose por Pólizas",
        "Ranking de Cuadrillas / Técnicos",
    ])

    # --- SUBTAB 1: EVOLUCIÓN & PRODUCTIVIDAD ---
    with sub_tab1:
        # KPI Cards
        st.markdown(f"""
        <div class="kpi-grid-4">
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">Órdenes Totales</div>
                <div class="kpi-card-value">{kpis.total_eventos:,}</div>
                <div class="kpi-card-subtitle">Folios únicos completados tras filtros</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">Técnicos Activos</div>
                <div class="kpi-card-value">{kpis.total_usuarios:,}</div>
                <div class="kpi-card-subtitle">Usuarios únicos en el período</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">Días Cuadrilla</div>
                <div class="kpi-card-value">{kpis.dias_operativos}</div>
                <div class="kpi-card-subtitle">Suma de días reales por usuario filtrado</div>
            </div>
            <div class="kpi-card-enterprise">
                <div class="kpi-card-title">Productividad / Día</div>
                <div class="kpi-card-value" style="color:{PALETA_COLOR['turquesa_cyan']} !important;">{kpis.productividad_diaria}</div>
                <div class="kpi-card-subtitle">Eventos completados ÷ días-cuadrilla</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        toggle_modo = st.radio(
            "Vista de barras:",
            ["Eventos Completados", "Cuadrillas Únicas con Actividad"],
            horizontal=True, key="radio_modo_barras_evolucion"
        )

        mask_g = df_folios[dimension_sel].notnull() & (
            ~df_folios[dimension_sel].astype(str).str.lower().isin(["nan","none","null",""])
        )
        fig_ev = generar_figura_evolucion_temporal(
            df_folios[mask_g], dimension_sel,
            modo_barras=toggle_modo,
            semanas_filtradas=semanas_filtradas,
            meses_filtrados=meses_filtrados,
        )
        st.plotly_chart(fig_ev, use_container_width=True, key="grafico_evolucion_temporal",
                        config={"displayModeBar": False, "scrollZoom": False})

        nom_dim = {"FECHA_TRUNCADA":"Día","SEMANA_DIM":"Semana","MES_DIM":"Mes","AÑO_DIM":"Año"}.get(dimension_sel,"Período")
        # La tabla siempre muestra la misma vista que el toggle del gráfico
        es_cuadrillas = (toggle_modo == "Cuadrillas Únicas con Actividad")
        etiqueta_vista = "CUADRILLAS ÚNICAS (TÉCNICOS)" if es_cuadrillas else "EVENTOS COMPLETADOS"
        subtitulo_tbl  = "Técnicos únicos con actividad por período y proveedor." if es_cuadrillas else "Total de órdenes completadas por período y proveedor."

        st.markdown(f"""
        <div class="matrix-title-card">
            <b>{etiqueta_vista} POR {nom_dim.upper()} Y PROVEEDOR</b>
            <p>{subtitulo_tbl}</p>
        </div>""", unsafe_allow_html=True)

        if not df_folios.empty:
            # Pivot: técnicos únicos o eventos completados según toggle
            agg_func_mz = "nunique" if es_cuadrillas else "count"
            val_col_mz  = "Usuario_Tecnico" if es_cuadrillas else "FOLIO_KEY"

            df_mz = pd.pivot_table(
                df_folios, index="Empresa", columns=dimension_sel,
                values=val_col_mz, aggfunc=agg_func_mz, fill_value=0
            )
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

            # Fila de totales
            if es_cuadrillas:
                fila_tot = df_folios.groupby(dimension_sel)["Usuario_Tecnico"].nunique().reindex(cols_ord).fillna(0).astype(int)
            else:
                fila_tot = df_folios.groupby(dimension_sel)["FOLIO_KEY"].count().reindex(cols_ord).fillna(0).astype(int)

            row_dict = dict(zip(cols_ord, fila_tot.values))
            row_dict["Tendencia"]        = fila_tot.tolist()
            row_dict["Promedio Período"] = round(float(fila_tot.mean()), 1)
            # TOTAL GENERAL separado: se añade DESPUÉS del sort para que no se mueva
            df_mz_data = df_mz.copy()
            df_mz_total = pd.DataFrame([row_dict], index=["TOTAL GENERAL"])

            cols_fin = ["Tendencia"] + cols_ord + ["Promedio Período"]
            etiq_col = "Empresa / Proveedor"
            # Unir: datos ordenados + total al final (fijo)
            df_mz_final = pd.concat([df_mz_data[cols_fin], df_mz_total[cols_fin]])
            st.dataframe(
                df_mz_final.reset_index().rename(columns={"index": etiq_col}),
                column_config={
                    etiq_col:           st.column_config.Column(width="medium", pinned=True),
                    "Tendencia":        st.column_config.LineChartColumn(width="small", y_min=0, pinned=True),
                    "Promedio Período": st.column_config.NumberColumn(format="%.1f"),
                },
                use_container_width=True, hide_index=True, height=320
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

            # Descarga del dataset filtrado actual
            st.download_button(
                "Descargar dataset filtrado (CSV)",
                df_folios.to_csv(index=False).encode("utf-8"),
                f"Dataset_Filtrado_{ANIO_BASE_ESTRICTO}.csv", "text/csv",
                key="dl_dataset_subtab1"
            )

    # --- SUBTAB 2: DESGLOSE PÓLIZAS ---
    # Esta tabla tiene sus PROPIOS filtros — no le afectan los filtros del sidebar.
    # Fuente de datos: df_raw completo (pasado desde main como argumento).
    with sub_tab2:
        st.markdown("### Desglose Operativo por Póliza, Distrito y Proveedor")
        st.caption(
            "Filtros independientes del sidebar. "
            "Vivo = cuadrillas únicas (técnicos únicos con actividad en la combinación "
            "Distrito × Empresa × Póliza para la semana seleccionada). "
            "Balance = Sugerido − Vivo."
        )

        if not df_raw.empty and "SEMANA_DIM" in df_raw.columns:
            # ---- Filtros propios ----
            sems_raw   = sorted(df_raw["SEMANA_DIM"].dropna().unique(), key=_num_sem)
            # Default: última semana registrada
            sem_deflt  = [sems_raw[-1]] if sems_raw else []

            cf1, cf2, cf3, cf4 = st.columns(4)
            with cf1:
                sem_sel_pol = st.multiselect(
                    "Semana:", sems_raw, default=sem_deflt, key="pol_sem_sel"
                )
            with cf2:
                dist_raw   = sorted(df_raw["Distrito"].dropna().unique())
                dist_sel_pol = st.multiselect("Distrito:", dist_raw, key="pol_dist_sel")
            with cf3:
                emp_raw    = sorted(df_raw["Empresa"].dropna().unique())
                emp_sel_pol = st.multiselect("Empresa / Proveedor:", emp_raw, key="pol_emp_sel")
            with cf4:
                pol_raw    = sorted([k for k in df_raw["Codigo_Poliza"].dropna().unique() if k in MAPEO_POLIZAS])
                pol_sel_pol = st.multiselect("Póliza:", pol_raw, key="pol_pol_sel")

            # Aplicar filtros propios
            df_pol_base = df_raw.copy()
            if sem_sel_pol:  df_pol_base = df_pol_base[df_pol_base["SEMANA_DIM"].isin(sem_sel_pol)]
            if dist_sel_pol: df_pol_base = df_pol_base[df_pol_base["Distrito"].isin(dist_sel_pol)]
            if emp_sel_pol:  df_pol_base = df_pol_base[df_pol_base["Empresa"].isin(emp_sel_pol)]
            if pol_sel_pol:  df_pol_base = df_pol_base[df_pol_base["Codigo_Poliza"].isin(pol_sel_pol)]

            if df_pol_base.empty:
                st.info("Sin datos para los filtros seleccionados.")
            else:
                df_base_pol = (
                    df_pol_base
                    .groupby(["Distrito","Empresa","Codigo_Poliza","Nombre_Poliza"], observed=True)
                    .agg(
                        Vivo=("Usuario_Tecnico","nunique"),
                        Eventos=("FOLIO_KEY","count"),
                    )
                    .reset_index()
                    .sort_values(["Distrito","Empresa","Codigo_Poliza"])
                    .rename(columns={"Codigo_Poliza":"Nomenclatura","Nombre_Poliza":"Modalidad"})
                )

                # Sugeridos persistidos en session_state
                clave_sug = "sugeridos_poliza"
                if clave_sug not in st.session_state:
                    st.session_state[clave_sug] = {}

                col_tbl, col_sug = st.columns([0.72, 0.28])
                with col_sug:
                    st.markdown("**Cuadrillas Sugeridas**")
                    st.caption("Meta por Distrito · Empresa · Póliza.")
                    for _, row in df_base_pol.iterrows():
                        fila_key  = f"{row['Distrito']}|{row['Empresa']}|{row['Nomenclatura']}"
                        val_prev  = st.session_state[clave_sug].get(fila_key, int(row["Vivo"]))
                        nuevo_val = st.number_input(
                            f"{row['Distrito'][:10]} · {row['Empresa'][:12]} · {row['Nomenclatura']}",
                            min_value=0, value=val_prev,
                            key=f"sug_{fila_key}", label_visibility="visible"
                        )
                        st.session_state[clave_sug][fila_key] = nuevo_val

                with col_tbl:
                    def _get_sug(row):
                        k = f"{row['Distrito']}|{row['Empresa']}|{row['Nomenclatura']}"
                        return st.session_state[clave_sug].get(k, int(row["Vivo"]))

                    df_base_pol["Sugerido"] = df_base_pol.apply(_get_sug, axis=1)
                    df_base_pol["Balance"]  = df_base_pol["Sugerido"] - df_base_pol["Vivo"]

                    def _color_balance(val):
                        if val < 0:  return "color:#EF4444;font-weight:700;"
                        if val == 0: return "color:#FBBF24;font-weight:700;"
                        return "color:#10B981;font-weight:700;"

                    df_show_pol = df_base_pol[
                        ["Distrito","Empresa","Nomenclatura","Modalidad","Sugerido","Vivo","Balance","Eventos"]
                    ]
                    st.dataframe(
                        df_show_pol.style.applymap(_color_balance, subset=["Balance"]),
                        use_container_width=True, hide_index=True, height=500,
                        column_config={
                            "Distrito":     st.column_config.Column(width="small",  pinned=True),
                            "Empresa":      st.column_config.Column(width="medium"),
                            "Nomenclatura": st.column_config.Column(width="small"),
                            "Modalidad":    st.column_config.Column(width="medium"),
                            "Sugerido":     st.column_config.NumberColumn(width="small"),
                            "Vivo":         st.column_config.NumberColumn(width="small"),
                            "Balance":      st.column_config.NumberColumn(width="small"),
                            "Eventos":      st.column_config.NumberColumn(width="small"),
                        }
                    )
                    st.download_button(
                        "Descargar desglose (CSV)",
                        df_show_pol.to_csv(index=False).encode("utf-8"),
                        "Desglose_Polizas.csv","text/csv"
                    )
        else:
            st.info("Sin datos disponibles en el repositorio.")

    # --- SUBTAB 3: RANKING ---
    with sub_tab3:
        st.markdown("### Ranking de Productividad por Técnico / Cuadrilla")
        st.caption("Solo pólizas elegibles (D1, D6, E3, M3, M4, MT, R3). Excluye proveedores ITAI, TOTALBOX y TOTALPLAY. Excluye generación-25.")
        if not df_folios.empty:
            col_rk1, col_rk2 = st.columns([0.6, 0.4])
            with col_rk1:
                ord_rk = st.radio("Ordenar por:", ["Productividad Diaria","Eventos Totales","Días Activos"],
                                  horizontal=True, key="orden_ranking")
            with col_rk2:
                dir_rk = st.radio("Dirección:", ["Mayor a menor","Menor a mayor"],
                                  horizontal=True, key="dir_ranking")
            asc_rk = (dir_rk == "Menor a mayor")
            col_ord_map = {
                "Productividad Diaria": "Productividad_Diaria",
                "Eventos Totales":      "Eventos_Totales",
                "Días Activos":         "Dias_Activos",
            }

            df_rk = (df_folios.groupby(
                ["Usuario_Tecnico","Distrito","Empresa","Codigo_Poliza","Nombre_Poliza"], observed=True
            ).agg(
                Eventos_Totales=("FOLIO_KEY","count"),
                Dias_Activos=("FECHA_TRUNCADA","nunique"),
            ).reset_index())

            # Productividad por técnico = Eventos / Días-activos propios (no días globales)
            df_rk["Productividad_Diaria"] = np.where(
                df_rk["Dias_Activos"] > 0,
                np.round(df_rk["Eventos_Totales"] / df_rk["Dias_Activos"], 2),
                0.0
            )
            df_rk = df_rk.sort_values(col_ord_map[ord_rk], ascending=asc_rk)
            df_rk.columns = [
                "Técnico","Distrito","Empresa","Póliza","Modalidad",
                "Eventos Totales","Días Activos","Productividad Diaria"
            ]
            st.dataframe(
                df_rk,
                use_container_width=True, hide_index=True, height=440,
                column_config={
                    "Técnico":             st.column_config.Column(width="large", pinned=True),
                    "Distrito":            st.column_config.Column(width="small"),
                    "Empresa":             st.column_config.Column(width="medium"),
                    "Póliza":              st.column_config.Column(width="small"),
                    "Modalidad":           st.column_config.Column(width="medium"),
                    "Eventos Totales":     st.column_config.NumberColumn(width="small"),
                    "Días Activos":        st.column_config.NumberColumn(width="small"),
                    "Productividad Diaria":st.column_config.NumberColumn(format="%.2f", width="small"),
                }
            )
            st.download_button(
                "Descargar ranking (CSV)",
                df_rk.to_csv(index=False).encode("utf-8"),
                "Ranking_Cuadrillas.csv","text/csv"
            )




# ==============================================================================
# 11. MÓDULO DE CARGA A GITHUB
# ==============================================================================

def renderizar_modulo_carga_github():
    st.markdown("""<style>
    /* ── Contenedor completo del formulario de carga ── */
    .st-key-formulario_carga,
    .st-key-formulario_carga * {
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
    }
    /* Labels de todos los campos */
    .st-key-formulario_carga label,
    .st-key-formulario_carga .stMarkdown p,
    .st-key-formulario_carga .stCaption p {
        color: #1E3E62 !important;
        font-weight: 600 !important;
    }
    /* Selectbox / Dropdown */
    .st-key-formulario_carga [data-testid="stSelectbox"] > div,
    .st-key-formulario_carga [data-testid="stSelectbox"] input,
    .st-key-formulario_carga [data-testid="stSelectbox"] [role="combobox"],
    .st-key-formulario_carga [data-baseweb="select"] > div {
        background: #ffffff !important;
        color: #000000 !important;
        border: 1px solid #CBD5E1 !important;
        border-radius: 7px !important;
    }
    /* Opciones del dropdown */
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="listbox"],
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="option"] {
        background-color: #ffffff !important;
        color: #000000 !important;
    }
    body:has(.st-key-formulario_carga) [data-baseweb="popover"] [role="option"]:hover {
        background-color: #E2E8F0 !important;
    }
    /* Radio buttons */
    .st-key-formulario_carga [data-testid="stRadio"] label,
    .st-key-formulario_carga [data-testid="stRadio"] p {
        color: #000000 !important;
        font-weight: 500 !important;
    }
    /* Text inputs */
    .st-key-formulario_carga [data-testid="stTextInput"] input,
    .st-key-formulario_carga [data-testid="stTextArea"] textarea {
        background-color: #ffffff !important;
        color: #000000 !important;
        border: 1px solid #CBD5E1 !important;
        border-radius: 7px !important;
    }
    /* Password input */
    .st-key-formulario_carga [data-testid="stTextInput"] input[type="password"] {
        background-color: #ffffff !important;
        color: #000000 !important;
    }
    /* Caption / info */
    .st-key-formulario_carga [data-testid="stCaptionContainer"] p {
        color: #475569 !important;
    }
    /* Botón principal de guardar */
    .st-key-formulario_carga div.stButton > button[kind="primary"] {
        background-color: #1E3E62 !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 700 !important;
    }
    /* Métricas dentro del formulario */
    .st-key-formulario_carga [data-testid="stMetricValue"] {
        color: #1E3E62 !important;
    }
    .st-key-formulario_carga [data-testid="stMetricLabel"] {
        color: #475569 !important;
    }
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
            ruta_candidato = prefijo + candidato
            if ruta_candidato in inventario:
                st.warning(
                    f"**`{candidato}`** ya aparece en el inventario del repositorio. "
                    "Si lo borraste de GitHub y quieres volver a cargarlo, confirma abajo. "
                    "Si solo quieres actualizarlo con nuevas filas, elige 'Actualizar un CSV existente'."
                )
                sobrescribir = st.checkbox(
                    "Sí, quiero sobrescribir / volver a crear este archivo",
                    key="chk_sobrescribir_csv"
                )
                if sobrescribir:
                    nombre_csv = candidato   # permitir la carga
                    # Limpiar inventario para que no haya falsos positivos
                    inventario_repositorio_github.clear()
            else:
                nombre_csv = candidato

    destino = f"{carpeta}/{nombre_csv}" if carpeta and nombre_csv else None
    if destino:
        st.info(f"Destino: `{GITHUB_REPO}/{destino}`")

    clave = st.text_input("Clave de autorización:", type="password", key="token_auth_carga_v2")

    st.caption(
        "El Parquet histórico se genera localmente con `generar_parquet.py`. "
        "El dashboard sólo consulta el archivo consolidado para mantener una apertura rápida."
    )

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

        st.info(
            "El CSV quedó guardado como respaldo. Para actualizar el dashboard, "
            "ejecuta `generar_parquet.py` en VS Code y sube el Parquet resultante."
        )

        st.cache_data.clear()
        inventario_repositorio_github.clear()
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
    <div class="kpi-grid-4">
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

        # Filtro de técnicos por cuenta seleccionada (se conecta con la sección de cuentas)
        cuenta_filtro_tech = st.session_state.get("cuenta_activa_rein", None)
        if cuenta_filtro_tech:
            techs_de_cuenta = set(
                df_fr[df_fr["Cuenta_Cliente"] == cuenta_filtro_tech]["Usuario_Origen_Reincidencia"].dropna()
            )
            df_agt_vis = df_agt[df_agt["Técnico Reincidente (Origen)"].isin(techs_de_cuenta)]
            st.info(f"Mostrando técnicos involucrados en cuenta {cuenta_filtro_tech}. "
                    f"({len(df_agt_vis)} de {len(df_agt)})")
            if st.button("Ver todos los técnicos", key="btn_clear_cuenta_tech"):
                st.session_state["cuenta_activa_rein"] = None
                st.rerun()
        else:
            df_agt_vis = df_agt

        col_t1, col_t2 = st.columns([0.75, 0.25])
        with col_t1:
            st.dataframe(df_agt_vis[["Técnico Reincidente (Origen)","Empresa","Eventos Completados",
                                     "Total Reincidencias","% Efectividad","Causas_TIPO_2","Fallas_Nuevas"]],
                         use_container_width=True, hide_index=True, height=360,
                         column_config={"% Efectividad": st.column_config.NumberColumn(format="%.2f %%")})
        with col_t2:
            st.markdown("**Ver detalle ampliado:**")
            tech_lista = sorted(df_agt_vis["Técnico Reincidente (Origen)"].unique())
            tech_sel   = st.selectbox("Seleccionar técnico:", tech_lista, key="sb_pop_tech") if tech_lista else None
            if tech_sel and st.button("Abrir detalle", use_container_width=True, key="btn_pop_tech"):
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

            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("Ver historial completo", type="primary",
                             disabled=(cuenta_sel is None), use_container_width=True,
                             key="btn_popup_cuenta"):
                    mostrar_modal_detalle_cuenta(
                        df_fr[df_fr["Cuenta_Cliente"] == cuenta_sel], str(cuenta_sel)
                    )
            with col_btn2:
                if st.button("Filtrar técnicos", type="secondary",
                             disabled=(cuenta_sel is None), use_container_width=True,
                             key="btn_filtrar_tech_por_cuenta"):
                    st.session_state["cuenta_activa_rein"] = cuenta_sel
                    st.rerun()

        # Descarga completa
        st.download_button(
            "Descargar todas las cuentas (CSV)",
            df_cta.to_csv(index=False).encode("utf-8"),
            "Reincidencias_por_Cuenta.csv", "text/csv"
        )
    else:
        st.info("Sin historial de cuentas con reincidencia para mostrar.")


# ==============================================================================
# 12a. MÓDULO CAMBIOS DE EQUIPO
# ==============================================================================

def renderizar_pestana_cambios_equipo(df_folios: pd.DataFrame, df_raw: pd.DataFrame, dimension_sel: str) -> None:
    """
    Módulo Cambios de Equipo.
    Efectividad = Cambio de equipo sin Soporte con falla "Cambio de ONT" en 60 días posteriores.
    Estructura preparada para conexión futura con consumo de materiales.
    """
    st.markdown("### Cambios de Equipo — Control de Efectividad")

    # Filtros independientes
    fi1, fi2, fi3, fi4 = st.columns(4)
    df_ce_base = df_raw.copy() if not df_raw.empty else df_folios.copy()

    sems_ce   = sorted(df_ce_base.get("SEMANA_DIM", pd.Series()).dropna().unique() if "SEMANA_DIM" in df_ce_base.columns else [], key=_num_sem)
    distr_ce  = sorted(df_ce_base["Distrito"].dropna().unique()) if "Distrito" in df_ce_base.columns else []
    emp_ce    = sorted(df_ce_base["Empresa"].dropna().unique())  if "Empresa"  in df_ce_base.columns else []

    with fi1:
        sel_sem_ce  = st.multiselect("Semana:", sems_ce,  key="ce_sem")
    with fi2:
        sel_dist_ce = st.multiselect("Distrito:", distr_ce, key="ce_dist")
    with fi3:
        sel_emp_ce  = st.multiselect("Empresa:", emp_ce,  key="ce_emp")
    with fi4:
        ventana_dias = st.number_input("Ventana de efectividad (días):", 1, 180, 60, key="ce_ventana")

    df_ce = df_ce_base.copy()
    if sel_sem_ce:  df_ce = df_ce[df_ce["SEMANA_DIM"].isin(sel_sem_ce)]
    if sel_dist_ce: df_ce = df_ce[df_ce["Distrito"].isin(sel_dist_ce)]
    if sel_emp_ce:  df_ce = df_ce[df_ce["Empresa"].isin(sel_emp_ce)]

    # Cambios de equipo
    mask_ce = df_ce["Tipo_Orden"].str.upper().str.contains("CAMBIO DE EQUIPO", na=False)
    df_ce_ev = df_ce[mask_ce].copy()
    total_ce = len(df_ce_ev)

    # Soporte con falla "Cambio de ONT" (buscar en Falla_Registro o Causa_Registro)
    mask_sop = df_ce["Tipo_Orden"].str.upper().str.contains("SOPORTE", na=False)
    df_sop   = df_ce[mask_sop].copy()
    col_falla_ce = "Falla_Registro" if "Falla_Registro" in df_sop.columns else None
    if col_falla_ce:
        mask_ont = df_sop[col_falla_ce].str.upper().str.contains("ONT", na=False)
        df_sop_ont = df_sop[mask_ont].copy()
    else:
        df_sop_ont = pd.DataFrame()

    # Cruzar: cambios de equipo que tienen un soporte ONT posterior en < ventana_dias
    tiene_fechas = "_datetime_parsed" in df_ce.columns and "_datetime_termino" in df_ce.columns
    ce_fallidos = 0
    if not df_ce_ev.empty and not df_sop_ont.empty and tiene_fechas:
        for _, ce_row in df_ce_ev.iterrows():
            f_cierre_ce = ce_row.get("_datetime_termino")
            cta = ce_row.get("Cuenta_Cliente")
            if pd.isna(f_cierre_ce) or pd.isna(cta):
                continue
            sop_misma_cta = df_sop_ont[df_sop_ont["Cuenta_Cliente"] == cta]
            for _, sop_row in sop_misma_cta.iterrows():
                f_sop = sop_row.get("_datetime_parsed")
                if pd.notna(f_sop):
                    delta = (pd.Timestamp(f_sop) - pd.Timestamp(f_cierre_ce)).days
                    if 0 < delta <= ventana_dias:
                        ce_fallidos += 1
                        break

    ce_efectivos = total_ce - ce_fallidos
    tasa_efect   = round(ce_efectivos / total_ce * 100, 1) if total_ce > 0 else 0.0

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    _col_ce = st.columns(4)
    for _c, _titulo, _val, _sub, _col in [
        (_col_ce[0], "Total Cambios de Equipo",       f"{total_ce:,}",    "En el período filtrado",          PALETA_COLOR["turquesa_cyan"]),
        (_col_ce[1], f"Fallidos (ONT ≤{ventana_dias}d)", f"{ce_fallidos:,}", "Con reincidencia de falla ONT",   PALETA_COLOR["amarillo_sol"]),
        (_col_ce[2], "Efectivos",                     f"{ce_efectivos:,}","Sin falla ONT posterior",         PALETA_COLOR["verde_montana"]),
        (_col_ce[3], "Tasa de Efectividad",           f"{tasa_efect}%",   "Cambios sin falla posterior",     PALETA_COLOR["turquesa_cyan"]),
    ]:
        _c.markdown(f"""<div class="kpi-card-enterprise">
            <div class="kpi-card-title">{_titulo}</div>
            <div class="kpi-card-value" style="color:{_col} !important;">{_val}</div>
            <div class="kpi-card-subtitle">{_sub}</div>
        </div>""", unsafe_allow_html=True)

    # Gráfico de línea de tiempo
    if not df_ce_ev.empty and "SEMANA_DIM" in df_ce_ev.columns:
        st.markdown("---")
        sems_ord_ce = sorted(df_ce_ev["SEMANA_DIM"].dropna().unique(), key=_num_sem)
        cnt_ce = df_ce_ev.groupby("SEMANA_DIM").size().reindex(sems_ord_ce, fill_value=0)
        tend_ce = calcular_tendencia_lineal_robusta(cnt_ce.tolist())

        fig_ce = go.Figure()
        fig_ce.add_trace(go.Bar(
            x=sems_ord_ce, y=cnt_ce.values,
            name="Cambios de Equipo", marker_color=PALETA_COLOR["azul_marina"], opacity=0.85,
        ))
        fig_ce.add_trace(go.Scatter(
            x=sems_ord_ce, y=tend_ce, name="Tendencia",
            mode="lines", line=dict(color=PALETA_COLOR["turquesa_cyan"], width=2, dash="dash"),
        ))
        fig_ce.update_layout(
            paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
            font=dict(family="Arial, sans-serif", size=11, color="#000000"),
            margin=dict(l=50, r=30, t=40, b=120),
            title=dict(text="<b>CAMBIOS DE EQUIPO POR SEMANA</b>",
                       x=0.01, font=dict(size=13, color="#000000")),
            xaxis=dict(tickangle=-45, tickfont=dict(size=9, color="#000000"),
                       type="category", categoryorder="array", categoryarray=sems_ord_ce),
            yaxis=dict(showgrid=True, gridcolor="#E2E8F0", tickfont=dict(color="#000000", size=10)),
            legend=dict(orientation="h", yanchor="top", y=-0.35, xanchor="center", x=0.5),
            dragmode=False,
        )
        st.plotly_chart(fig_ce, use_container_width=True, key="fig_cambios_equipo",
                        config={"displayModeBar": False})

    # Tabla por técnico
    st.markdown("---")
    st.markdown("#### Detalle por Técnico")
    if not df_ce_ev.empty:
        df_ce_tech = (df_ce_ev.groupby(["Usuario_Tecnico","Empresa","Distrito"], observed=True)
                      .size().reset_index(name="Cambios de Equipo")
                      .sort_values("Cambios de Equipo", ascending=False))
        st.dataframe(df_ce_tech, use_container_width=True, hide_index=True, height=360)
        st.download_button("Descargar tabla (CSV)",
                           df_ce_tech.to_csv(index=False).encode("utf-8"),
                           "Cambios_Equipo_Tecnicos.csv","text/csv")

    # PLACEHOLDER: conexión futura con consumo de materiales
    st.markdown("---")
    st.markdown("""<div class="matrix-title-card">
        <b>CONSUMO DE MATERIALES — ESTRUCTURA PREPARADA</b>
        <p>Este módulo se conectará con el archivo de consumo de materiales cuando esté disponible.
        La lógica validará si la falla incluye consumo de equipo (ONT u otro dispositivo)
        para correlacionar el cambio físico con la reincidencia posterior.</p>
    </div>""", unsafe_allow_html=True)
    st.info("Pendiente de definición del nombre y ubicación del archivo de materiales. "
            "Al conectarlo, se cruzará automáticamente con los cambios de equipo registrados aquí.")


# ==============================================================================
# 12b. MÓDULO CAUSA Y SOLUCIÓN SOPORTE
# ==============================================================================

def renderizar_pestana_causa_solucion(df_folios: pd.DataFrame, dimension_sel: str) -> None:
    """
    Módulo Causa y Solución Soporte.
    Solo considera eventos de tipo SOPORTE.
    Pareto 80/20 con jerarquía dinámica (Causa / Falla / Solución).
    """
    st.markdown("### Causa y Solución — Soporte")

    # Solo SOPORTE
    df_sop = df_folios[df_folios["Tipo_Orden"].str.upper().str.contains("SOPORTE", na=False)].copy()
    if df_sop.empty:
        st.info("Sin registros de tipo Soporte para los filtros activos.")
        return

    # Verificar columnas disponibles
    tiene_causa   = "Causa_Registro"    in df_sop.columns
    tiene_falla   = "Falla_Registro"    in df_sop.columns
    tiene_sol     = "Solucion_Registro" in df_sop.columns

    if not (tiene_causa or tiene_falla or tiene_sol):
        st.warning("Los archivos cargados no contienen columnas de Causa, Falla o Solución. "
                   "Verifica que el CSV incluya esas columnas.")
        return

    # Rellenar valores faltantes
    for col, nombre in [("Causa_Registro","SIN CAUSA"),("Falla_Registro","SIN FALLA"),
                         ("Solucion_Registro","SIN SOLUCION")]:
        if col not in df_sop.columns:
            df_sop[col] = nombre
        else:
            df_sop[col] = df_sop[col].fillna(nombre).replace("SIN ESPECIFICAR", nombre)

    DIMS = {"Causa": "Causa_Registro", "Falla": "Falla_Registro", "Solución": "Solucion_Registro"}

    # ---- Controles ----
    st.markdown("#### Configuración del Pareto")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        nivel1 = st.selectbox("Nivel 1 (eje principal):", list(DIMS.keys()), index=0, key="cs_n1")
    niv_rest = [k for k in DIMS if k != nivel1]
    with c2:
        nivel2 = st.selectbox("Nivel 2:", niv_rest, index=0, key="cs_n2")
    nivel3 = [k for k in DIMS if k not in [nivel1, nivel2]][0]
    with c3:
        st.markdown(f"**Nivel 3:** {nivel3}")

    col_n1 = DIMS[nivel1]; col_n2 = DIMS[nivel2]; col_n3 = DIMS[nivel3]

    with c4:
        top_n = st.slider("Top N categorías:", 5, 30, 15, key="cs_topn")

    # Filtros internos de la sección (multiselect independientes)
    fi1, fi2, fi3 = st.columns(3)
    with fi1:
        opts_causa = sorted(df_sop["Causa_Registro"].dropna().unique())
        sel_causa  = st.multiselect("Filtrar Causa:", opts_causa, key="cs_fil_causa")
    with fi2:
        opts_falla = sorted(df_sop["Falla_Registro"].dropna().unique())
        sel_falla  = st.multiselect("Filtrar Falla:", opts_falla, key="cs_fil_falla")
    with fi3:
        opts_sol   = sorted(df_sop["Solucion_Registro"].dropna().unique())
        sel_sol    = st.multiselect("Filtrar Solución:", opts_sol, key="cs_fil_sol")

    df_f = df_sop.copy()
    if sel_causa: df_f = df_f[df_f["Causa_Registro"].isin(sel_causa)]
    if sel_falla: df_f = df_f[df_f["Falla_Registro"].isin(sel_falla)]
    if sel_sol:   df_f = df_f[df_f["Solucion_Registro"].isin(sel_sol)]

    if df_f.empty:
        st.info("Sin registros para los filtros seleccionados.")
        return

    # ---- PARETO ----
    st.markdown("---")
    st.markdown(f"#### Pareto 80/20 — {nivel1} como eje principal")

    df_pareto = (df_f.groupby(col_n1, observed=True)
                 .size().reset_index(name="Frecuencia")
                 .sort_values("Frecuencia", ascending=False)
                 .head(top_n))
    df_pareto["% Acumulado"] = (df_pareto["Frecuencia"].cumsum() /
                                 df_pareto["Frecuencia"].sum() * 100).round(1)

    fig_pareto = go.Figure()
    fig_pareto.add_trace(go.Bar(
        x=df_pareto[col_n1], y=df_pareto["Frecuencia"],
        name="Frecuencia", marker_color=PALETA_COLOR["azul_marina"],
        text=df_pareto["Frecuencia"], textposition="outside",
        textfont=dict(size=9, color="#000000"),
    ))
    fig_pareto.add_trace(go.Scatter(
        x=df_pareto[col_n1], y=df_pareto["% Acumulado"],
        name="% Acumulado", yaxis="y2", mode="lines+markers",
        line=dict(color=PALETA_COLOR["turquesa_cyan"], width=2),
        marker=dict(size=6, color=PALETA_COLOR["azul_noche"]),
    ))
    # Línea 80%
    fig_pareto.add_hline(y=80, yref="y2", line_dash="dash",
                          line_color=PALETA_COLOR["verde_montana"], line_width=1.5,
                          annotation_text="80%", annotation_position="top right",
                          annotation_font_color="#000000")
    fig_pareto.update_layout(
        barmode="group",
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        font=dict(family="Arial, sans-serif", size=11, color="#000000"),
        margin=dict(l=50, r=60, t=40, b=160),
        title=dict(text=f"<b>PARETO DE {nivel1.upper()} — TOP {top_n}</b>",
                   x=0.01, font=dict(size=13, color="#000000")),
        legend=dict(orientation="h", yanchor="top", y=-0.45, xanchor="center", x=0.5,
                    font=dict(size=10, color="#000000")),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9, color="#000000"),
                   showgrid=False, type="category"),
        yaxis=dict(title="Frecuencia", showgrid=True, gridcolor="#E2E8F0",
                   tickfont=dict(color="#000000", size=10)),
        yaxis2=dict(title="% Acumulado", overlaying="y", side="right",
                    range=[0, 105], showgrid=False,
                    tickfont=dict(color="#000000", size=10)),
        dragmode=False,
    )
    st.plotly_chart(fig_pareto, use_container_width=True, key="pareto_causa_sol",
                    config={"displayModeBar": False})

    # ---- TABLA DESGLOSE ----
    st.markdown(f"#### Desglose {nivel1} → {nivel2} → {nivel3}")
    df_desg = (df_f.groupby([col_n1, col_n2, col_n3], observed=True)
               .size().reset_index(name="Casos")
               .sort_values("Casos", ascending=False))
    df_desg.columns = [nivel1, nivel2, nivel3, "Casos"]
    st.dataframe(df_desg, use_container_width=True, hide_index=True, height=320)

    # ---- TÉCNICOS CON REINCIDENCIAS EN SOPORTE ----
    st.markdown("---")
    st.markdown("#### Técnicos — Reincidencias y Efectividad (filtrado a Soporte)")
    if "ES_REINCIDENCIA" in df_f.columns and "Usuario_Tecnico" in df_f.columns:
        df_tech_sop = (df_f.groupby("Usuario_Tecnico", observed=True)
                       .agg(Total_Soportes=("FOLIO_KEY","count"),
                            Reincidencias=("ES_REINCIDENCIA", lambda x: (x=="SI").sum()))
                       .reset_index())
        df_tech_sop["Efectividad_%"] = np.where(
            df_tech_sop["Total_Soportes"] > 0,
            np.round((1 - df_tech_sop["Reincidencias"] / df_tech_sop["Total_Soportes"]) * 100, 1),
            100.0
        )
        df_tech_sop = df_tech_sop.sort_values("Reincidencias", ascending=False)
        df_tech_sop.columns = ["Técnico","Total Soportes","Reincidencias","% Efectividad"]
        st.dataframe(df_tech_sop, use_container_width=True, hide_index=True, height=320,
                     column_config={"% Efectividad": st.column_config.NumberColumn(format="%.1f %%")})
        st.download_button("Descargar tabla técnicos soporte (CSV)",
                           df_tech_sop.to_csv(index=False).encode("utf-8"),
                           "Soporte_Tecnicos.csv","text/csv")


# ==============================================================================
# 13. FUNCIÓN PRINCIPAL (main)
# ==============================================================================

def main():
    inyectar_estilos_css_enterprise()

    st.markdown(f"""
    <div class="main-header-enterprise">
        <h1>OPERACIONES — REGIÓN NORTE LA BAJA</h1>
        <p>Módulo Consolidado de Analítica, Pólizas y Control Técnico de Campo ({ANIO_BASE_ESTRICTO})</p>
    </div>""", unsafe_allow_html=True)

    # Indicador de frescura del parquet vs CSV más reciente
    try:
        r_check = requests.head(
            f"{GITHUB_RAW_BASE}/datos_consolidados.parquet",
            headers=HEADERS, timeout=5, allow_redirects=True
        )
        parquet_ok = r_check.status_code == 200
    except Exception:
        parquet_ok = False

    if parquet_ok:
        st.sidebar.success("Dataset consolidado disponible")
    else:
        st.sidebar.warning("Sin parquet consolidado — carga lenta activa")

    if st.sidebar.button("Actualizar datos", key="actualizar_parquet_dashboard"):
        st.cache_data.clear()
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
    st.sidebar.caption("Cada filtro reduce las opciones de los siguientes.")
    df_t = df_raw.copy()

    # ── 1. MES ───────────────────────────────────────────────────────────────
    meses_disp = [m for m in LISTA_ORDENADA_MESES if m in df_t["MES_DIM"].unique()]
    sel_meses  = st.sidebar.multiselect(
        f"Mes: ({len(meses_disp)} disponibles)", meses_disp, key="f_mes"
    )
    if sel_meses: df_t = df_t[df_t["MES_DIM"].isin(sel_meses)]

    # ── 2. SEMANA (opciones reducidas por mes seleccionado) ──────────────────
    sems_disp  = sorted(df_t["SEMANA_DIM"].dropna().unique(), key=_num_sem)
    sel_sems   = st.sidebar.multiselect(
        f"Semana: ({len(sems_disp)} disponibles)", sems_disp, key="f_sem"
    )
    if sel_sems:
        # Primero se identifica el cierre semanal y enseguida se valida la fecha
        # real de cada orden. Así, en la vista Día, Sem 38 sólo puede mostrar
        # 14–21 de septiembre; productividad y días-cuadrilla usan ese mismo
        # subconjunto, además de Distrito y Póliza seleccionados más abajo.
        df_t = df_t[df_t["SEMANA_DIM"].isin(sel_sems)]
        df_t, rangos_semana = acotar_fechas_a_semanas_seleccionadas(df_t, sel_sems)
        if rangos_semana:
            st.sidebar.caption("Fechas operativas: " + " · ".join(rangos_semana))

    # ── 3. PÓLIZA ─────────────────────────────────────────────────────────────
    pols_disp   = sorted([k for k in df_t["Codigo_Poliza"].dropna().unique() if k in MAPEO_POLIZAS])
    opc_pol     = [f"{c} — {MAPEO_POLIZAS[c]}" for c in pols_disp]
    default_pol = [op for op in opc_pol if not any(op.startswith(exc) for exc in POLIZAS_DEFAULT_EXCLUIDAS)]
    sel_pol     = st.sidebar.multiselect(
        f"Póliza: ({len(opc_pol)} disponibles)", opc_pol,
        default=default_pol, key="f_pol"
    )
    cods_pol = [p.split(" — ")[0] for p in sel_pol]
    if cods_pol: df_t = df_t[df_t["Codigo_Poliza"].isin(cods_pol)]

    # ── 4. TIPO DE EVENTO ─────────────────────────────────────────────────────
    tipos_ev  = sorted(df_t["Tipo_Orden"].dropna().unique())
    sel_tipos = st.sidebar.multiselect(
        f"Tipo de Evento: ({len(tipos_ev)} disponibles)", tipos_ev, key="f_tipo"
    )
    if sel_tipos: df_t = df_t[df_t["Tipo_Orden"].isin(sel_tipos)]

    # ── 5. DISTRITO ───────────────────────────────────────────────────────────
    distr_disp = sorted(df_t["Distrito"].dropna().unique())
    sel_distr  = st.sidebar.multiselect(
        f"Distrito: ({len(distr_disp)} disponibles)", distr_disp, key="f_dist"
    )
    if sel_distr: df_t = df_t[df_t["Distrito"].isin(sel_distr)]

    # ── 6. EMPRESA / PROVEEDOR ────────────────────────────────────────────────
    emps_disp = sorted(df_t["Empresa"].dropna().unique())
    sel_emps  = st.sidebar.multiselect(
        f"Empresa: ({len(emps_disp)} disponibles)", emps_disp, key="f_emp"
    )
    if sel_emps: df_t = df_t[df_t["Empresa"].isin(sel_emps)]

    # ── 7. CLUSTER ────────────────────────────────────────────────────────────
    if "Cluster_Base" in df_t.columns:
        clusters_disp = sorted([
            c for c in df_t["Cluster_Base"].dropna().unique()
            if str(c).upper() not in ["SIN CLUSTER","CLUSTER GENERAL",""]
        ])
        sel_clusters = st.sidebar.multiselect(
            f"Cluster: ({len(clusters_disp)} disponibles)",
            clusters_disp, key="f_cluster"
        )
        if sel_clusters: df_t = df_t[df_t["Cluster_Base"].isin(sel_clusters)]

    # ── 8. TÉCNICO (código — ID primario) ────────────────────────────────────
    if "Usuario_Tecnico" in df_t.columns:
        df_t["_cod_tecnico"] = df_t["Usuario_Tecnico"].str.split(" | ").str[0].str.strip()
        cods_tech_disp = sorted(df_t["_cod_tecnico"].dropna().unique())
        sel_techs = st.sidebar.multiselect(
            f"Técnico: ({len(cods_tech_disp)} disponibles)",
            cods_tech_disp, key="f_tecnico"
        )
        if sel_techs: df_t = df_t[df_t["_cod_tecnico"].isin(sel_techs)]
        df_t = df_t.drop(columns=["_cod_tecnico"], errors="ignore")

    # Indicador de registros activos
    st.sidebar.markdown("---")
    st.sidebar.metric("Registros activos", f"{len(df_t):,}", f"de {len(df_raw):,} totales")

    df_folios = df_t.drop_duplicates(subset=["FOLIO_KEY"], keep="first")

    tab1, tab2, tab3, tab4 = st.tabs([
        "Pólizas y Cuadrillas",
        "Reincidencias",
        "Cambios de Equipo",
        "Causa y Solución Soporte",
    ])

    with tab1:
        renderizar_pestana_polizas_cuadrillas(
            df_folios, df_raw, dimension_sel,
            semanas_filtradas=sel_sems,
            meses_filtrados=sel_meses,
        )
    with tab2:
        renderizar_pestana_reincidencias_total(df_folios, dimension_sel)
    with tab3:
        renderizar_pestana_cambios_equipo(df_folios, df_raw, dimension_sel)
    with tab4:
        renderizar_pestana_causa_solucion(df_folios, dimension_sel)



if __name__ == "__main__":
    main()
