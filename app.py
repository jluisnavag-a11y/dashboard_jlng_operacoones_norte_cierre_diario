import streamlit as st
import pandas as pd
import numpy as np
import io
import re
import os
import glob
import plotly.express as px

# ==============================================================================
# SISTEMA ENTERPRISE DE CONTROL OPERATIVO DE CUADRILLAS EN CAMPO 2026
# Archivo: app.py
# Versión: 13.4.0-FORCE-REFRESH Totalplay Región Norte La Baja Edition
# ==============================================================================

import os
import glob
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass

import plotly.graph_objects as go
from supabase import create_client, Client

@st.cache_resource
def get_supabase_client() -> Optional[Client]:
    try:
        url = "https://rmvfhhtugdvdkadickpf.supabase.co"
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.warning(f"No se pudo conectar a Supabase Secrets: {e}")
        return None

supabase = get_supabase_client()
st.set_page_config(layout="wide")

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ControlCuadrillas.Monolith")

# ==============================================================================
# 1. CONSTANTES GLOBALES Y DICCIONARIOS DE NEGOCIO
# ==============================================================================

ANIO_BASE_ESTRICTO: int = 2026
EXCEL_EPOCH_START: pd.Timestamp = pd.Timestamp("1899-12-30")
NOMBRE_SISTEMA: str = "TOTALPLAY / OPERACIONES - REGIÓN NORTE LA BAJA"
VERSION_SISTEMA: str = "13.4.0-FORCE-REFRESH"

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
# 3. ESTILOS CSS UNIFICADOS (MODO CLARO CONTROLADO CON BORDES ENTERPRISE)
# ==============================================================================

def inyectar_estilos_css_enterprise() -> None:
    css_custom = f"""
    <style>
    @import url("https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap");

    html, body, [class*="css"], .stApp {{
        font-family: "Plus Jakarta Sans", -apple-system, sans-serif !important;
        background-color: #F8FAFC !important;
        color: #000000 !important;
    }}

    [data-testid="stSidebar"] {{
        background-color: {PALETA_COLOR["azul_noche"]} !important;
        min-width: 320px !important;
    }}
    [data-testid="stSidebar"] * {{
        color: #FFFFFF !important;
    }}

    .main-header-enterprise {{
        background: linear-gradient(135deg, {PALETA_COLOR["azul_noche"]} 0%, {PALETA_COLOR["azul_marina"]} 100%);
        padding: 24px 30px;
        border-radius: 14px;
        border-bottom: 4px solid {PALETA_COLOR["turquesa_cyan"]};
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
        color: {PALETA_COLOR["turquesa_cyan"]} !important;
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
        border: 2px solid {PALETA_COLOR["azul_marina"]} !important;
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
        background: {PALETA_COLOR["turquesa_cyan"]};
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
    }}

    .kpi-card-title {{
        font-weight: 800 !important;
        color: {PALETA_COLOR["azul_marina"]} !important;
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

    .stTabs [data-baseweb="tab-list"] {{
        gap: 6px;
        background-color: {PALETA_COLOR["azul_marina"]};
        padding: 6px;
        border-radius: 10px;
    }}

    .stTabs [data-baseweb="tab"] {{
        height: 42px;
        background-color: transparent;
        border-radius: 6px;
        color: #FFFFFF !important;
        font-weight: 700;
        font-size: 13px;
    }}

    .stTabs [aria-selected="true"] {{
        background-color: {PALETA_COLOR["turquesa_cyan"]} !important;
        color: {PALETA_COLOR["azul_noche"]} !important;
        font-weight: 800 !important;
    }}

    /* FORZAR ESTILO HOMOGÉNEO Y CLARO EN TODAS LAS TABLAS DATAFRAME */
    div[data-testid="stDataFrame"], div[aria-label="st.dataframe"] {{
        background-color: #FFFFFF !important;
        border: 2px solid {PALETA_COLOR["azul_marina"]} !important;
        border-radius: 12px !important;
        padding: 4px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05) !important;
    }}

    div[data-testid="stDataFrame"] iframe {{
        background-color: #FFFFFF !important;
    }}

    .matrix-title-card {{
        background-color: #FFFFFF;
        border-left: 5px solid {PALETA_COLOR["naranja_desierto"]};
        border-top: 1px solid #E2E8F0;
        border-right: 1px solid #E2E8F0;
        border-bottom: 1px solid #E2E8F0;
        padding: 12px 16px;
        border-radius: 6px;
        margin-top: 15px;
        margin-bottom: 12px;
       box-shadow: 0 2px 5px rgba(0,0,0,0.03);
    }}

    /* Botones secundarios homologados (Limpiar Caché y CSV) */
    div.stButton > button[kind="secondary"], 
    div.stButton > button:not([kind="primary"]),
    div.stDownloadButton > button {{
        background-color: #FFFFFF !important;
        color: #1E293B !important;
        border: 1px solid #CBD5E1 !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        box-shadow: 0px 2px 4px rgba(0,0,0,0.05) !important;
    }}

    div.stButton > button[kind="secondary"]:hover, 
    div.stButton > button:not([kind="primary"]):hover,
    div.stDownloadButton > button:hover {{
        background-color: #F8FAFC !important;
        border-color: #94A3B8 !important;
        color: #0F172A !important;
    }}
    /* Botón de Cargar Archivo (st.file_uploader) */
        [data-testid="stFileUploader"] section button,
        [data-testid="stFileUploader"] label button {{
            background-color: #FFFFFF !important;
            color: #1E293B !important;
            border: 1px solid #CBD5E1 !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            box-shadow: 0px 2px 4px rgba(0,0,0,0.05) !important;
        }}

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

def _cargar_archivo_robusto(ruta: str) -> Optional[pd.DataFrame]:
    nombre = os.path.basename(ruta)
    df_temp = None

    if ruta.lower().endswith(".csv"):
        encodings = ["utf-8-sig", "utf-8", "latin1", "cp1252"]
        separadores = [",", ";", "\t"]

        for enc in encodings:
            for sep in separadores:
                try:
                    df_temp = pd.read_csv(ruta, encoding=enc, sep=sep, low_memory=False)
                    if df_temp is not None and not df_temp.empty and len(df_temp.columns) > 1:
                        break
                except Exception:
                    continue
            if df_temp is not None and not df_temp.empty and len(df_temp.columns) > 1:
                break
    else:
        try:
            df_temp = pd.read_excel(ruta)
        except Exception as e:
            logger.error(f"Error cargando Excel {nombre}: {e}")

    if df_temp is not None and not df_temp.empty:
        df_temp["Archivo_Origen"] = nombre
        return df_temp
    
    return None

def obtener_hash_archivos_carpeta(carpeta: str) -> str:
    """Genera una firma única en tiempo real basada en archivos y sus fechas de modificación."""
    if not os.path.exists(carpeta):
        return "sin_carpeta"
    archivos = sorted(glob.glob(os.path.join(carpeta, "*.csv")) + glob.glob(os.path.join(carpeta, "*.xlsx")))
    info = []
    for f in archivos:
        try:
            stat = os.stat(f)
            info.append(f"{f}:{stat.st_mtime}:{stat.st_size}")
        except Exception:
            pass
    return "|".join(info)

import concurrent.futures
import io
import pandas as pd
import streamlit as st

@st.cache_data(ttl=86400, show_spinner="Descargando datos de la nube...")
def obtener_archivos_supabase():
    try:
        # 1. Obtener la lista de archivos en el bucket
        archivos = supabase.storage.from_("Totalplay_datos_semanales").list()
        csv_files = [f['name'] for f in archivos if f['name'].endswith('.csv')]
        
        if not csv_files:
            return [], set()

        # Función interna para descargar un solo archivo en su propio hilo
        def descargar_individual(nombre_archivo):
            try:
                res = supabase.storage.from_("Totalplay_datos_semanales").download(nombre_archivo)
                # Engine C de Pandas para lectura ultrarrápida
                df_temp = pd.read_csv(io.BytesIO(res), engine='c', low_memory=False)
                if not df_temp.empty:
                    df_temp["Archivo_Origen"] = nombre_archivo
                    return df_temp, nombre_archivo.upper()
            except Exception as e:
                pass
            return None, None

        # 2. Descargar todos los CSV simultáneamente (Multithreading)
        coleccion_dfs = []
        archivos_procesados = set()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            resultados = list(executor.map(descargar_individual, csv_files))

        for df_res, nom_res in resultados:
            if df_res is not None:
                coleccion_dfs.append(df_res)
                archivos_procesados.add(nom_res)

        return coleccion_dfs, archivos_procesados

    except Exception as e:
        st.error(f"Error al conectar con Supabase Storage: {e}")
        return [], set()

@st.cache_data(ttl=3600, show_spinner="Procesando datos y optimizando memoria...")
def ejecutar_pipeline_ingestion_datos(hash_archivos: str = "") -> pd.DataFrame:
    coleccion_dfs = []
    archivos_procesados = set()

    # 1. Descargar archivos de Supabase
    dfs_nube, archivos_procesados_nube = obtener_archivos_supabase()
    coleccion_dfs.extend(dfs_nube)
    archivos_procesados.update(archivos_procesados_nube)

    # 2. Cargar de la carpeta local (solo los que aún no estén en Supabase)
    carpeta_origen = "datos_semanales"
    if os.path.exists(carpeta_origen):
        archivos_locales = sorted(glob.glob(os.path.join(carpeta_origen, "*.csv")))
        for ruta in archivos_locales:
            nombre_local = os.path.basename(ruta).upper()
            if nombre_local not in archivos_procesados:
                df_c = _cargar_archivo_robusto(ruta)
                if df_c is not None and not df_c.empty:
                    df_c["Archivo_Origen"] = os.path.basename(ruta)
                    coleccion_dfs.append(df_c)

    if not coleccion_dfs:
        return pd.DataFrame()

    df = pd.concat(coleccion_dfs, ignore_index=True)
    df.columns = [str(col).strip() for col in df.columns]
    cols = list(df.columns)

    # Normalización de Fecha compatible con Supabase / GitHub / Excel
    col_fecha = detectar_columna_por_patrones(cols, LISTA_ALIAS_CREACION)
    if col_fecha and col_fecha in df.columns:
        es_num = pd.to_numeric(df[col_fecha], errors='coerce')
        fechas_excel = pd.to_datetime('1899-12-30') + pd.to_timedelta(es_num, unit='D', errors='coerce')
        fechas_texto = pd.to_datetime(df[col_fecha], errors='coerce', dayfirst=True, format='mixed')
        df["_datetime_parsed"] = fechas_excel.fillna(fechas_texto)
    else:
        df["_datetime_parsed"] = pd.NaT

    # Detección y normalización de columnas principales
    col_os = detectar_columna_por_patrones(cols, LISTA_ALIAS_ORDEN)
    col_cta = detectar_columna_por_patrones(cols, LISTA_ALIAS_CUENTA)
    col_ot = detectar_columna_por_patrones(cols, LISTA_ALIAS_OT)
    col_tipo = detectar_columna_por_patrones(cols, LISTA_ALIAS_TIPO)
    col_usr = detectar_columna_por_patrones(cols, LISTA_ALIAS_USUARIO)
    col_nom = detectar_columna_por_patrones(cols, LISTA_ALIAS_NOMBRE)
    col_prov = detectar_columna_por_patrones(cols, LISTA_ALIAS_PROVEEDOR)
    col_dist = detectar_columna_por_patrones(cols, LISTA_ALIAS_DISTRITO)
    col_cluster = detectar_columna_por_patrones(cols, LISTA_ALIAS_CLUSTER)
        
    serie_os = df[col_os].apply(sanitizar_folio_identificador) if col_os else "SIN_OS"
    serie_cta = df[col_cta].apply(sanitizar_folio_identificador) if col_cta else "SIN_CTA"
    serie_ot = df[col_ot].apply(sanitizar_folio_identificador) if col_ot else "SIN_OT"
    serie_tipo = df[col_tipo].apply(sanitizar_cadena_texto) if col_tipo else "EVENTO GENERAL"

    df["FOLIO_KEY"] = serie_os.astype(str) + "_" + serie_cta.astype(str) + "_" + serie_ot.astype(str) + "_" + serie_tipo.astype(str)
    df["Cuenta_Cliente"] = serie_cta.astype(str)
    
    serie_u = df[col_usr].apply(sanitizar_cadena_texto) if col_usr else "SIN ESPECIFICAR"
    serie_n = df[col_nom].apply(sanitizar_cadena_texto) if col_nom else "SIN ESPECIFICAR"
    
    df["Usuario_Tecnico"] = np.where(
        (serie_u != "SIN ESPECIFICAR") & (serie_n != "SIN ESPECIFICAR"),
        serie_u + " | " + serie_n,
        np.where(serie_n != "SIN ESPECIFICAR", serie_n, serie_u)
    )

    df["Empresa"] = df[col_prov].apply(sanitizar_cadena_texto) if col_prov else "SIN PROVEEDOR"
    df["Distrito"] = df[col_dist].apply(sanitizar_cadena_texto) if col_dist else "DISTRITO GENERAL"
    df["Tipo_Orden"] = serie_tipo

    df["Cluster_Raw"] = df[col_cluster].apply(sanitizar_cadena_texto) if col_cluster else "SIN CLUSTER"
    df["Cluster_Base"] = normalizar_clusters_vectorizado(df["Cluster_Raw"])

    col_origen_pol = col_usr if col_usr else col_nom
    if col_origen_pol:
        sub_cods = df[col_origen_pol].astype(str).str[3:5]
        df["Codigo_Poliza"] = np.where(sub_cods.isin(MAPEO_POLIZAS.keys()), sub_cods, "")
        df["Nombre_Poliza"] = df["Codigo_Poliza"].map(MAPEO_POLIZAS).fillna("NO VALIDO")
    else:
        df["Codigo_Poliza"] = ""
        df["Nombre_Poliza"] = "NO VALIDO"

    # Extracción y Dimensiones Temporales
    semanas_iso = df["_datetime_parsed"].dt.isocalendar().week
    semanas_archivo = df["Archivo_Origen"].apply(extraer_numero_semana_archivo) if "Archivo_Origen" in df.columns else 0
    df["Num_Semana_Archivo"] = pd.to_numeric(semanas_iso.fillna(semanas_archivo), errors="coerce").fillna(0).astype(int)

    df["AÑO_DIM"] = str(ANIO_BASE_ESTRICTO)
    df["SEMANA_DIM"] = df["Num_Semana_Archivo"].apply(lambda x: f"Semana {int(x)}" if x > 0 else "SIN_FECHA")
    df["MES_DIM"] = df["_datetime_parsed"].dt.month.fillna(1).astype(int).map(MAPEO_MESES_TEXTO)
    
    dates_valid = df["_datetime_parsed"].dropna()
    df["FECHA_TRUNCADA"] = f"01.01.{ANIO_BASE_ESTRICTO}"
    if not dates_valid.empty:
        df.loc[dates_valid.index, "FECHA_TRUNCADA"] = dates_valid.dt.strftime(f"%d.%m.{ANIO_BASE_ESTRICTO}")

    df = calcular_reincidencias_vectorizadas(df)

    return df

# ==============================================================================
# CSS DE ALTO IMPACTO (COMPATIBLE CON STREAMLIT CLOUD Y LOCALHOST)
# ==============================================================================

def inyectar_estilos_base_ui():
    """Inyecta CSS global forzado usando selectores nativos de Streamlit (.stTabs)
    para evitar bloqueos por Shadow DOM o librerías dinámicas de React/BaseWeb.
    """
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

        /* 3. HOVER (CUANDO EL MOUSE PASA POR ENCIMA) */
        div[data-testid="stTabs"] button[role="tab"]:hover {
            background-color: #334155 !important;
            border-color: #475569 !important;
        }
        div[data-testid="stTabs"] button[role="tab"]:hover p {
            color: #f8fafc !important;
        }

        /* 4. PESTAÑA ACTIVA (SELECCIONADA) */
        div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
            background-color: #0284c7 !important;
            border-color: #38bdf8 !important;
            box-shadow: 0 4px 12px rgba(2, 132, 199, 0.4) !important;
        }

        div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p {
            color: #ffffff !important;
            font-weight: 700 !important;
        }

        /* ELIMINAR LÍNEA INFERIOR ROJA/AZUL NATIVA DE STREAMLIT */
        div[data-testid="stTabs"] div[data-baseweb="tab-highlight"] {
            display: none !important;
        }

        /* 5. FIX PARA TEXTAREA Y INPUTS EN MODO OSCURO */
        div[data-testid="stTextArea"] textarea, div[data-testid="stTextInput"] input {
            background-color: #0f172a !important;
            color: #f8fafc !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
            font-family: monospace !important;
        }
        
        div[data-testid="stTextArea"] textarea:focus, div[data-testid="stTextInput"] input:focus {
            border-color: #38bdf8 !important;
            box-shadow: 0 0 0 1px #38bdf8 !important;
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
        "☁️ Cargar Datos (Supabase)"
    ])

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

    # --------------------------------------------------------------------------
    # SUBTAB 5: CARGAR DATOS (SUPABASE)
    # --------------------------------------------------------------------------
    with sub_tab5:
        CLAVE_ACCESO_CARGA = "Totalplay1#Norte"

        st.subheader("🚀 Cargar Nueva Semana a Supabase Storage")

        nombre_archivo_input = st.text_input(
            "Nombre del archivo CSV (ej. CIERRE DIARIO SEM 26 2026):", 
            value="CIERRE DIARIO SEM 27 2026"
        )

        contenido_csv_input = st.text_area(
            "Pega el contenido copiado directamente desde Excel para subir el archivo CSV a la nube:",
            height=250
        )

        clave_ingresada = st.text_input(
            "🔒 Ingrese la clave de autorización para confirmar la subida:", 
            type="password"
        )

        if st.button("🚀 Guardar y Subir", type="primary", width="stretch"):
            if not nombre_archivo_input.strip() or not contenido_csv_input.strip():
                st.warning("⚠️ Debe proporcionar tanto el nombre del archivo como el contenido CSV.")
            elif clave_ingresada != CLAVE_ACCESO_CARGA:
                st.error("❌ Clave de autorización incorrecta. No se realizaron cambios en Supabase.")
            else:
                try:
                    with st.spinner("Procesando y subiendo datos a Supabase Storage..."):
                        nombre_f = nombre_archivo_input.strip()
                        if not nombre_f.lower().endswith(".csv"):
                            nombre_f += ".csv"
                        
                        bytes_data = contenido_csv_input.encode("utf-8")
                        res = supabase.storage.from_("Totalplay_datos_semanales").upload(
                            path=nombre_f,
                            file=bytes_data,
                            file_options={"upsert": "true", "content-type": "text/csv"}
                        )
                        st.cache_data.clear()
                        st.success(f"✅ ¡Archivo '{nombre_f}' guardado y subido con éxito! El caché ha sido actualizado.")
                        st.balloons()
                except Exception as e:
                    st.error(f"❌ Error al intentar subir el archivo a Supabase: {e}")


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
            Causas_TIPO_2=("TIPO_2", lambda x: " | ".join(sorted(set(str(v).strip() for v in x if pd.notna(v) and str(v).strip() != "")))),
            Fallas_Nuevas=("Falla_Nueva", lambda x: " | ".join(sorted(set(str(v).strip() for v in x if pd.notna(v) and str(v).strip() != ""))))
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


def guardar_y_reemplazar_semana_texto(nombre_semana: str, texto_datos: str) -> bool:
    """Procesa el buffer en texto plano e interactúa con Supabase Storage de manera atómica."""
    try:
        texto_limpio = texto_datos.strip()
        if not texto_limpio:
            st.error("El cuadro de texto está vacío.")
            return False

        try:
            df_nuevo = pd.read_csv(io.StringIO(texto_limpio), sep="\t", dtype=str)
            if len(df_nuevo.columns) <= 1:
                df_nuevo = pd.read_csv(io.StringIO(texto_limpio), sep=",", dtype=str)
        except Exception as e_parse:
            st.error(f"Error al interpretar la estructura de la tabla: {e_parse}")
            return False

        df_nuevo.columns = df_nuevo.columns.astype(str).str.strip()

        nombre_semana_clean = nombre_semana.strip().upper()
        df_nuevo["Num_Semana_Archivo"] = nombre_semana_clean
        if "SEMANA" not in df_nuevo.columns:
            df_nuevo["SEMANA"] = nombre_semana_clean

        csv_buffer = io.StringIO()
        df_nuevo.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
        bytes_datos = csv_buffer.getvalue().encode("utf-8-sig")

        nombre_archivo = f"{nombre_semana_clean}.csv"
        
        if supabase:
            supabase.storage.from_("Totalplay_datos_semanales").upload(
                path=nombre_archivo,
                file=bytes_datos,
                file_options={"content-type": "text/csv; charset=utf-8", "upsert": "true"}
            )
            st.cache_data.clear()
            return True
        else:
            st.error("No hay una conexión activa con Supabase.")
            return False

    except Exception as e:
        st.error(f"Error crítico al subir la semana a Supabase: {e}")
        return False
# ==============================================================================
# 8. NAVEGACIÓN PRINCIPAL
# ==============================================================================

def main() -> None:
    st.set_page_config(
        page_title=f"{NOMBRE_SISTEMA} v{VERSION_SISTEMA}",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # --------------------------------------------------------------------------
    # ESTILO FORZADO PARA PESTAÑAS (TABS) - VISIBILIDAD TOTAL EN CUALQUIER TEMA
    # --------------------------------------------------------------------------
    st.markdown("""
        <style>
        /* Contenedor principal de la barra de pestañas */
        div[data-baseweb="tab-list"] {
            background-color: #0d1117 !important;
            padding: 8px !important;
            border-radius: 12px !important;
            border: 1px solid #1e293b !important;
            gap: 8px !important;
        }

        /* Pestañas inactivas (Estilo botón oscuro con borde sutil) */
        button[data-baseweb="tab"] {
            background-color: #161b22 !important;
            border: 1px solid #30363d !important;
            border-radius: 8px !important;
            padding: 10px 18px !important;
            white-space: nowrap !important;
            position: relative !important;
            transition: all 0.2s ease-in-out !important;
        }

        /* Texto de pestañas inactivas */
        button[data-baseweb="tab"] p, 
        button[data-baseweb="tab"] span {
            color: #9198a1 !important;
            font-weight: 600 !important;
        }

        /* Hover al pasar el ratón */
        button[data-baseweb="tab"]:hover {
            background-color: #21262d !important;
            border-color: #38bdf8 !important;
        }

        /* Pestaña ACTIVA con la BARRA AZUL destacada */
        button[data-baseweb="tab"][aria-selected="true"] {
            background-color: #1e293b !important;
            border: 2px solid #0284c7 !important;
            box-shadow: 0px 0px 10px rgba(2, 132, 199, 0.4) !important;
        }

        /* Barra Azul superior en la pestaña activa */
        button[data-baseweb="tab"][aria-selected="true"]::before {
            content: "" !important;
            position: absolute !important;
            top: 0 !important;
            left: 0 !important;
            right: 0 !important;
            height: 4px !important;
            background-color: #38bdf8 !important;
            border-radius: 8px 8px 0 0 !important;
        }

        /* Texto de la pestaña activa */
        button[data-baseweb="tab"][aria-selected="true"] p,
        button[data-baseweb="tab"][aria-selected="true"] span {
            color: #38bdf8 !important;
            font-weight: 700 !important;
        }
        </style>
    """, unsafe_allow_html=True)

    inyectar_estilos_css_enterprise()

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
            if st.button("🔄 Recargar", use_container_width="stretch", type="primary"):
                st.cache_data.clear()
                st.rerun()
        with btn_c2:
            if st.button("🧹 Limpiar Caché", use_container_width="stretch", type="secondary"):
                st.cache_data.clear()
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

    # OBTENER FIRMA DINÁMICA DE LOS ARCHIVOS
    hash_actual = obtener_hash_archivos_carpeta("datos_semanales")
    df_raw = ejecutar_pipeline_ingestion_datos(hash_actual)

    if df_raw.empty:
        st.error("⚠️ No hay datos disponibles para procesar en Supabase o en la carpeta 'datos_semanales'. Verifique las conexiones y archivos.")
        return

    # --------------------------------------------------------------------------
    # BARRA LATERAL (SIDEBAR DE FILTROS A LA IZQUIERDA)
    # --------------------------------------------------------------------------
    st.sidebar.markdown("### 📅 Granularidad Temporal")
    dimension_sel = st.sidebar.radio(
        "Agrupar tiempo por:",
        ["SEMANA_DIM", "FECHA_TRUNCADA", "MES_DIM", "AÑO_DIM"],
        index=0,
        format_func=lambda x: {
            "SEMANA_DIM": "Semana (Semana 01)",
            "FECHA_TRUNCADA": "Fecha Truncada (dd.mm.2026)",
            "MES_DIM": "Mes (mmmm)",
            "AÑO_DIM": "Año (2026)"
        }[x]
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🔍 Multifiltros Dinámicos")

    df_temp = df_raw.copy()

    meses_disponibles = [m for m in LISTA_ORDENADA_MESES if m in df_temp["MES_DIM"].unique()]
    filtro_meses_sel = st.sidebar.multiselect("📅 Mes:", meses_disponibles)
    if filtro_meses_sel:
        df_temp = df_temp[df_temp["MES_DIM"].isin(filtro_meses_sel)]

    semanas_disponibles = sorted([str(s) for s in df_temp["SEMANA_DIM"].dropna().unique()])
    filtro_semanas_sel = st.sidebar.multiselect("📅 Semana:", semanas_disponibles)
    if filtro_semanas_sel:
        df_temp = df_temp[df_temp["SEMANA_DIM"].isin(filtro_semanas_sel)]

    polizas_existentes = sorted([k for k in df_temp["Codigo_Poliza"].unique() if k in MAPEO_POLIZAS])
    opciones_poliza = [f"{cod} - {MAPEO_POLIZAS[cod]}" for cod in polizas_existentes]
    filtro_polizas_sel = st.sidebar.multiselect("📌 Pólizas:", opciones_poliza)
    codigos_poliza_sel = [p.split(" - ")[0] for p in filtro_polizas_sel]
    if codigos_poliza_sel:
        df_temp = df_temp[df_temp["Codigo_Poliza"].isin(codigos_poliza_sel)]

    tipos_eventos = sorted(list(df_temp["Tipo_Orden"].unique()))
    filtro_eventos_sel = st.sidebar.multiselect("📌 Tipo de Evento / Orden:", tipos_eventos)
    if filtro_eventos_sel:
        df_temp = df_temp[df_temp["Tipo_Orden"].isin(filtro_eventos_sel)]

    distritos = sorted(list(df_temp["Distrito"].unique()))
    filtro_distritos = st.sidebar.multiselect("📍 Distrito / Zona:", distritos)
    if filtro_distritos:
        df_temp = df_temp[df_temp["Distrito"].isin(filtro_distritos)]

    empresas = sorted(list(df_temp["Empresa"].unique()))
    filtro_empresas = st.sidebar.multiselect("🏢 Proveedor / Empresa:", empresas)
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
        st.info("ℹ️ Módulo pendiente de configuración. Indica las reglas requeridas cuando gustes construirlo.")

    with tab4:
        st.markdown("### 🎧 Causa & Solución soporte")
        st.info("ℹ️ Módulo pendiente de configuración. Indica las reglas requeridas cuando gustes construirlo.")

if __name__ == "__main__":
    main()