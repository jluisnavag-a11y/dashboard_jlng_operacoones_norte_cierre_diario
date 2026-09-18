# ==============================================================================
# SISTEMA ENTERPRISE DE CONTROL OPERATIVO DE CUADRILLAS EN CAMPO 2026
# Archivo: app.py | Versión: 15.1.0-ANALITICA-RESTAURADA
# ==============================================================================

import os
import re
import io
import logging
import base64
import csv
import html
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
    layout="wide",
    initial_sidebar_state="auto",
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ControlCuadrillas.Monolith")    

import gc
import unicodedata
import json
import time

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




ARCHIVOS_CONSULTA = ("historico.parquet", "semana_actual.parquet")


class BaseNoPreparada(ValueError):
    pass


def api_github(metodo, ruta, **kwargs):
    respuesta = requests.request(metodo, f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}{ruta}",
                                 headers=_headers_github_contenido(), timeout=(10, 60), **kwargs)
    if not respuesta.ok:
        raise RuntimeError(f"GitHub respondió HTTP {respuesta.status_code}. Revisa acceso o cambios simultáneos y vuelve a intentar.")
    return respuesta.json()


@st.cache_data(ttl=60, show_spinner=False, max_entries=4)
def snapshot_consulta():
    commit = api_github("GET", f"/commits/{quote(GITHUB_BRANCH, safe='')}")
    tree = api_github("GET", f"/git/trees/{commit['commit']['tree']['sha']}?recursive=1")
    if tree.get("truncated"):
        raise RuntimeError("El inventario está incompleto; no se modificará la base.")
    return {"commit": commit["sha"], "tree": commit["commit"]["tree"]["sha"],
            "archivos": {f["path"]: f["sha"] for f in tree["tree"] if f["type"] == "blob"}}


@st.cache_data(show_spinner=False, max_entries=8)
def bytes_por_sha(sha):
    r = requests.get(f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/git/blobs/{sha}",
                     headers={**_headers_github_contenido(), "Accept": "application/vnd.github.raw+json"}, timeout=(10, 60))
    r.raise_for_status()
    contenido = r.content
    if "json" in r.headers.get("Content-Type", "").lower() and contenido.lstrip().startswith(b"{"):
        datos = r.json()
        if datos.get("encoding") == "base64":
            contenido = base64.b64decode(datos["content"])
    esperado = hashlib.sha1(b"blob " + str(len(contenido)).encode() + b"\0" + contenido).hexdigest()
    if esperado != sha:
        raise ValueError("La descarga no coincide con la revisión solicitada.")
    return contenido


def ruta_datos(nombre):
    return "/".join(p for p in (GITHUB_FOLDER.strip("/"), nombre) if p)


def leer_particiones(snapshot):
    salida = []
    for nombre in ARCHIVOS_CONSULTA:
        ruta = ruta_datos(nombre)
        if ruta not in snapshot["archivos"]:
            raise ValueError("La base de dos archivos todavía no está preparada. Abre Administración de datos para prepararla una vez.")
        df = pd.read_parquet(io.BytesIO(bytes_por_sha(snapshot["archivos"][ruta])))
        if "Archivo_Origen" not in df:
            raise ValueError("La base no identifica sus archivos de origen. Prepara nuevamente la base de consulta.")
        salida.append(df)
    return salida


@st.cache_data(show_spinner=False, max_entries=2)
def preparar_consulta(sha_historico, sha_actual, version):
    inicio = time.perf_counter()
    partes = [pd.read_parquet(io.BytesIO(bytes_por_sha(sha))) for sha in (sha_historico, sha_actual)]
    fuente = pd.concat(partes, ignore_index=True)
    df = transformar_dataset_completo(fuente)
    df.attrs["segundos_preparacion"] = round(time.perf_counter() - inicio, 2)
    df.attrs["fuentes"] = len(fuente["Archivo_Origen"].unique())
    return df


def ejecutar_pipeline_ingestion_datos(hash_archivos=""):
    snap = snapshot_consulta()
    rutas = [ruta_datos(n) for n in ARCHIVOS_CONSULTA]
    if not all(r in snap["archivos"] for r in rutas):
        raise BaseNoPreparada("Prepara la base de consulta en Administración de datos. La consulta habitual utilizará únicamente dos archivos.")
    return preparar_consulta(*(snap["archivos"][r] for r in rutas), VERSION_SISTEMA)


def publicar_particiones(historico, actual, snapshot):
    # Un único commit actualiza ambos archivos. No sobrescribe cambios concurrentes.
    entradas = []
    for nombre, df in zip(ARCHIVOS_CONSULTA, (historico, actual)):
        buf = io.BytesIO()
        df.to_parquet(buf, index=False, compression="snappy")
        contenido = buf.getvalue()
        sha = hashlib.sha1(b"blob " + str(len(contenido)).encode() + b"\0" + contenido).hexdigest()
        ruta = ruta_datos(nombre)
        if snapshot["archivos"].get(ruta) == sha:
            continue
        blob = api_github("POST", "/git/blobs", json={"content": base64.b64encode(contenido).decode(), "encoding": "base64"})
        if blob["sha"] != sha:
            raise ValueError("No se pudo verificar el archivo preparado.")
        entradas.append({"path": ruta, "mode": "100644", "type": "blob", "sha": sha})
    if not entradas:
        return snapshot["commit"]
    tree = api_github("POST", "/git/trees", json={"base_tree": snapshot["tree"], "tree": entradas})
    commit = api_github("POST", "/git/commits", json={"message": "Actualizar base operativa de dos archivos", "tree": tree["sha"], "parents": [snapshot["commit"]]})
    api_github("PATCH", f"/git/refs/heads/{quote(GITHUB_BRANCH, safe='')}", json={"sha": commit["sha"], "force": False})
    verificacion = api_github("GET", f"/git/trees/{tree['sha']}?recursive=1")
    mapa = {e["path"]: e["sha"] for e in verificacion["tree"]}
    if any(mapa.get(e["path"]) != e["sha"] for e in entradas):
        raise RuntimeError("No se pudo verificar la revisión guardada.")
    snapshot_consulta.clear()
    return commit["sha"]


def leer_fuente_snapshot(item):
    nombre, sha = item
    contenido = bytes_por_sha(sha)
    if nombre.lower().endswith(".parquet"):
        df = pd.read_parquet(io.BytesIO(contenido))
    else:
        try:
            texto = contenido.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = contenido.decode("latin1")
        df = interpretar_csv_pegado(texto)
    df["Archivo_Origen"] = nombre
    return df


def actualizar_particiones(nombre, nuevo, nueva_semana=False):
    snapshot_consulta.clear()
    snap = snapshot_consulta()
    historico, actual = leer_particiones(snap)
    confirmado = nuevo.attrs.get("guardado_github", {})
    if confirmado.get("sha") and snap["archivos"].get(ruta_datos(nombre)) != confirmado["sha"]:
        raise ValueError("El CSV cambió después de la captura. Actualiza la base desde la revisión más reciente.")
    nuevo = nuevo.copy()
    nuevo.attrs = {}
    nuevo["Archivo_Origen"] = nombre
    if nombre in set(actual["Archivo_Origen"]):
        actual = pd.concat([actual[actual["Archivo_Origen"] != nombre], nuevo], ignore_index=True)
    elif nombre in set(historico["Archivo_Origen"]):
        historico = pd.concat([historico[historico["Archivo_Origen"] != nombre], nuevo], ignore_index=True)
    elif nueva_semana:
        historico = pd.concat([historico, actual], ignore_index=True)
        actual = nuevo
    else:
        raise ValueError("El archivo es nuevo. Marca Iniciar nueva semana para incorporarlo a la base de consulta.")
    transformar_dataset_completo(pd.concat([historico, actual], ignore_index=True))
    return publicar_particiones(historico, actual, snap)


def administrar_base():
    st.subheader("Base de consulta")
    st.write("El histórico conserva las semanas cerradas. La semana activa recibe las actualizaciones. Ambas fuentes se cruzan antes de aplicar los filtros.")
    if st.button("Actualizar inventario", key="actualizar_inventario_base"):
        snapshot_consulta.clear()
    try:
        snap = snapshot_consulta()
    except Exception as exc:
        st.error(str(exc)); return
    prefijo = GITHUB_FOLDER.strip("/") + "/"
    fuentes = {r[len(prefijo):]: sha for r, sha in snap["archivos"].items()
               if r.startswith(prefijo) and "/" not in r[len(prefijo):] and r.lower().endswith((".csv", ".parquet"))
               and r[len(prefijo):] not in (*ARCHIVOS_CONSULTA, "datos_consolidados.parquet")}
    if not fuentes:
        st.info("No hay archivos semanales disponibles."); return
    seleccion = st.multiselect("Archivos semanales que integrarán la base", sorted(fuentes), default=sorted(fuentes), placeholder="Selecciona archivos")
    if not seleccion:
        return
    activa = st.selectbox("Archivo de la semana activa", seleccion, index=len(seleccion)-1)
    st.caption("Selecciona una sola fuente por semana. La preparación lee estos archivos una vez; las consultas posteriores leen solo los dos Parquet.")
    clave = st.text_input("Clave de autorización del portal", type="password", key="clave_base")
    if st.button("Preparar histórico y semana activa", type="primary"):
        if not GITHUB_TOKEN or clave != st.secrets.get("UPLOAD_PASSWORD", None):
            st.error("Revisa la clave de autorización y el token configurado."); return
        try:
            with st.status("Preparando base de consulta", expanded=True) as estado:
                st.write(f"Leyendo {len(seleccion)} archivos seleccionados.")
                with ThreadPoolExecutor(max_workers=4) as pool:
                    partes = list(pool.map(leer_fuente_snapshot, [(n, fuentes[n]) for n in seleccion]))
                actual = partes[seleccion.index(activa)]
                cerradas = [p for n, p in zip(seleccion, partes) if n != activa]
                historico = pd.concat(cerradas, ignore_index=True) if cerradas else actual.iloc[:0].copy()
                st.write("Validando fechas, órdenes y antecedentes entre semanas.")
                vista = transformar_dataset_completo(pd.concat([historico, actual], ignore_index=True))
                st.write(f"{len(vista):,} órdenes únicas. Guardando ambos archivos en una sola revisión.")
                publicar_particiones(historico, actual, snap)
                estado.update(label="Base de consulta preparada", state="complete", expanded=False)
            st.success("Histórico y semana activa guardados y verificados. Ya puedes abrir Consulta operativa.")
        except Exception as exc:
            st.error(f"No se completó la preparación: {exc}")


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
VERSION_SISTEMA: str = "15.1.0-ANALITICA-RESTAURADA"

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
    txt = reparar_codificacion(val).strip()
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
        serie_res.loc[mask_txt] = pd.to_datetime(sub_txt, dayfirst=True, format="mixed", errors="coerce")

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



# ==============================================================================
# 6. PIPELINE DE INGESTIÓN MULTI-ENCODING + ACTUALIZACIÓN FORZADA
# ==============================================================================

def reparar_codificacion(valor):
    if pd.isna(valor):
        return ""
    texto = str(valor).strip()
    for _ in range(3):
        if not any(c in texto for c in ("Ã", "Â", "√", "‚")):
            break
        for cod in ("latin1", "cp1252", "mac_roman"):
            try:
                reparado = texto.encode(cod).decode("utf-8")
                if reparado != texto:
                    texto = reparado
                    break
            except (UnicodeError, LookupError):
                pass
        else:
            break
    return unicodedata.normalize("NFC", texto)


def texto_canonico(valor):
    texto = unicodedata.normalize("NFKD", reparar_codificacion(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().upper()


def tipo_servicio(valor):
    clave = texto_canonico(valor)
    return {"INSTALACION": "Instalación", "INSTALACIONES": "Instalación",
            "SOPORTE": "Soporte", "CAMBIO DE DOMICILIO": "Cambio de domicilio",
            "CAMBIO DE EQUIPO": "Cambio de equipo"}.get(clave, clave.title())


TIPOS_EVALUABLES = {"Instalación", "Soporte", "Cambio de domicilio", "Cambio de equipo"}
ESTADOS_COMPLETOS = {"TERMINADA", "TERMINADO", "COMPLETADA", "COMPLETADO", "COMPLETA", "COMPLETO", "CERRADA", "CERRADO", "FINALIZADA", "FINALIZADO"}


def columna_exacta(df, opciones, requerida=True):
    mapa = {texto_canonico(c): c for c in df.columns}
    for opcion in opciones:
        if texto_canonico(opcion) in mapa:
            return mapa[texto_canonico(opcion)]
    if requerida:
        raise ValueError("Falta la columna: " + opciones[0])
    return None


def transformar_dataset_completo(df):
    if df.empty:
        raise ValueError("No hay registros para preparar.")
    df = df.copy().reset_index(drop=True)
    df.columns = df.columns.astype(str).str.strip()
    cuenta = columna_exacta(df, ["Cuenta", "Cuenta_Cliente"])
    os_col = columna_exacta(df, ["OS", "Orden servicio", "Orden_servicio"])
    ot = columna_exacta(df, ["OT", "Orden trabajo", "Orden_trabajo"])
    tipo = columna_exacta(df, ["Tipo", "Tipo de orden", "Tipo_Orden"])
    cierre = columna_exacta(df, ["Fecha termino", "Fecha término", "Fecha cierre", "Fecha fin", "closed_at"])
    estado = columna_exacta(df, ["Estatus", "Estado", "Estado de la orden"])
    df["Cuenta_Cliente"] = df[cuenta].map(sanitizar_folio_identificador)
    df["Tipo_Orden"] = df[tipo].map({v:tipo_servicio(v) for v in df[tipo].dropna().unique()}).fillna("Sin tipo")
    df["FOLIO_KEY"] = (df[os_col].map(sanitizar_folio_identificador) + "|" + df["Cuenta_Cliente"] + "|" +
                       df[ot].map(sanitizar_folio_identificador) + "|" + df["Tipo_Orden"])
    df["_datetime_termino"] = parsear_columna_fecha_robusta(df[cierre])
    creacion = columna_exacta(df, ["Fecha creacion FFM", "Fecha creación FFM", "Fecha creacion", "Fecha creación"], False)
    df["_datetime_parsed"] = parsear_columna_fecha_robusta(df[creacion]) if creacion else pd.NaT
    df["Orden_Completa"] = df[estado].map({v:texto_canonico(v) for v in df[estado].dropna().unique()}).isin(ESTADOS_COMPLETOS) & df["_datetime_termino"].notna()
    horas = (df["_datetime_termino"] - df["_datetime_parsed"]).dt.total_seconds() / 3600
    df["Tiempo_Resolucion_Horas"] = horas.where(horas.ge(0))
    for destino, opciones, defecto in [
        ("Empresa", ["Empresa(proveedor)", "Proveedor", "Empresa"], "Sin empresa"),
        ("Distrito", ["Distrito", "Zona"], "Sin distrito"),
        ("Cluster_Raw", ["Cluster", "Clúster"], "Sin zona"),
    ]:
        col = columna_exacta(df, opciones, False)
        df[destino] = df[col].map(sanitizar_cadena_texto) if col else defecto
    usuario = columna_exacta(df, ["Usuario instalador", "Usuario_instalador", "Usuario"], False)
    nombre = columna_exacta(df, ["Nombre tecnico", "Nombre técnico", "Nombre_tecnico"], False)
    u = df[usuario].fillna("").astype(str).str.replace(r"\s+", "", regex=True).str.upper() if usuario else pd.Series("", index=df.index)
    u = u.mask(u.isin(["NA", "N/A", "NAN", "NONE", "NULL", "SIN_ESPECIFICAR"]), "")
    n = df[nombre].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip() if nombre else pd.Series("", index=df.index)
    n = n.map({v:reparar_codificacion(v).upper() for v in n.unique()})
    n = n.mask(n.isin(["NA", "N/A", "NAN", "NONE", "NULL"]), "")
    df["Usuario_ID"] = u
    catalogo = pd.DataFrame({"usuario": u, "nombre": n, "fecha": df["_datetime_termino"]})
    catalogo = catalogo[catalogo["usuario"].ne("") & catalogo["nombre"].ne("")]
    catalogo = catalogo.sort_values(["fecha", "nombre"], na_position="first").drop_duplicates("usuario", keep="last").set_index("usuario")["nombre"]
    df["Nombre_Tecnico_Canonico"] = u.map(catalogo).fillna("Sin nombre")
    df["Usuario_Tecnico"] = (u + " | " + df["Nombre_Tecnico_Canonico"]).where(u.ne(""), "Sin usuario identificado")
    df["Codigo_Poliza"] = u.str[3:5].where(u.str[3:5].isin(MAPEO_POLIZAS), "")
    df["Nombre_Poliza"] = df["Codigo_Poliza"].map(MAPEO_POLIZAS).fillna("Sin póliza")
    df["Cluster_Base"] = normalizar_clusters_vectorizado(df["Cluster_Raw"])
    fecha = df["_datetime_termino"]
    iso = fecha.dt.isocalendar()
    df["Num_Semana_Archivo"] = iso.week.astype("Int64")
    df["SEMANA_DIM"] = iso.year.astype("string") + " · Semana " + iso.week.astype("string").str.zfill(2)
    df["MES_DIM"] = fecha.dt.strftime("%Y-%m")
    df["AÑO_DIM"] = fecha.dt.year.astype("Int64").astype("string")
    df["FECHA_TRUNCADA"] = fecha.dt.strftime("%Y-%m-%d")
    # Archivo de la semana activa se concatena al final & prevalece al actualizar.
    df = df.drop_duplicates("FOLIO_KEY", keep="last").reset_index(drop=True)
    return calcular_reincidencias_vectorizadas(df)


def calcular_reincidencias_vectorizadas(df):
    df = df.copy().reset_index(drop=True)
    for col in ["ES_REINCIDENCIA", "Usuario_Origen_Reincidencia", "Empresa_Origen_Reincidencia",
                "Semana_Origen_Reincidencia", "Causa_Origen", "TIPO_2", "Falla_Nueva",
                "Folio_Anterior", "Tipo_Anterior", "Revision_Cronologia"]:
        df[col] = ""
    df["Fecha_Cierre_Anterior"] = pd.NaT
    df["Dias_Entre_Visitas"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
    df["Numero_Visita"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
    valida = ~df["Cuenta_Cliente"].isin(["", "NA", "N/A", "NAN", "NONE", "SIN_FOLIO", "SIN_CTA", "NULL"])
    # Solo visitas completadas; los demás tipos permanecen para no saltar un antecedente no elegible.
    ordenadas = df.loc[valida & df["Orden_Completa"]].sort_values(["Cuenta_Cliente", "_datetime_termino", "FOLIO_KEY"])
    grupos = ordenadas.groupby("Cuenta_Cliente", sort=False)
    prev = grupos[["FOLIO_KEY", "Tipo_Orden", "_datetime_termino", "SEMANA_DIM", "Empresa", "Usuario_Tecnico"]].shift()
    empate = ordenadas.duplicated(["Cuenta_Cliente", "_datetime_termino"], keep=False)
    empate_previo = empate.groupby(ordenadas["Cuenta_Cliente"]).shift().eq(True)
    ambigua = empate | empate_previo
    df.loc[ordenadas.index[ambigua], "Revision_Cronologia"] = "Cierres simultáneos: revisar secuencia"
    numero = grupos.cumcount() + 1
    dias = (ordenadas["_datetime_termino"].dt.normalize() - prev["_datetime_termino"].dt.normalize()).dt.days
    cumple = (ordenadas["Tipo_Orden"].eq("Soporte") & prev["Tipo_Orden"].isin(TIPOS_EVALUABLES) &
              numero.ge(2) & dias.between(0, 60) & ~ambigua)
    df.loc[ordenadas.index, "Numero_Visita"] = numero.astype("Int64")
    idx = ordenadas.index[cumple]
    df.loc[idx, "ES_REINCIDENCIA"] = "SI"
    df.loc[idx, "Dias_Entre_Visitas"] = dias.loc[idx].astype("Int64")
    for destino, origen in [("Folio_Anterior", "FOLIO_KEY"), ("Tipo_Anterior", "Tipo_Orden"),
                            ("Fecha_Cierre_Anterior", "_datetime_termino"), ("Semana_Origen_Reincidencia", "SEMANA_DIM"),
                            ("Usuario_Origen_Reincidencia", "Usuario_Tecnico"), ("Empresa_Origen_Reincidencia", "Empresa")]:
        df.loc[idx, destino] = prev.loc[idx, origen]
    causa = columna_exacta(ordenadas, ["Causa", "Motivo", "Diagnostico"], False)
    falla = columna_exacta(ordenadas, ["Falla", "Observaciones", "Descripcion"], False)
    if causa:
        cprev = grupos[causa].shift()
        df.loc[idx, "Causa_Origen"] = cprev.loc[idx].fillna("")
        df.loc[idx, "TIPO_2"] = [obtener_valor_tipo2(c, t) for c, t in zip(cprev.loc[idx], prev.loc[idx, "Tipo_Orden"])]
    else:
        df.loc[idx, "TIPO_2"] = prev.loc[idx, "Tipo_Orden"]
    if falla:
        df.loc[idx, "Falla_Nueva"] = ordenadas.loc[idx, falla].fillna("")
    return df


def resumen_indicadores(df, dimensiones=None):
    base = df.loc[df["Orden_Completa"] & df["Tipo_Orden"].isin(TIPOS_EVALUABLES)].drop_duplicates("FOLIO_KEY").copy()
    base["Reincidentes"] = base["ES_REINCIDENCIA"].eq("SI").astype(int)
    if dimensiones:
        res = base.groupby(dimensiones, dropna=False, observed=True).agg(Completadas=("FOLIO_KEY", "size"), Reincidentes=("Reincidentes", "sum")).reset_index()
    else:
        res = pd.DataFrame({"Completadas": [len(base)], "Reincidentes": [int(base["Reincidentes"].sum())]})
    res["Reincidencia (%)"] = res["Reincidentes"].div(res["Completadas"].replace(0, np.nan)).mul(100)
    return res


def resumen_con_reincidencias_filtradas(base_df, reincidencias_df, dimensiones):
    """Conserva el denominador operativo y aplica filtros del antecedente solo al numerador."""
    base = base_df.loc[base_df["Orden_Completa"] & base_df["Tipo_Orden"].isin(TIPOS_EVALUABLES)].drop_duplicates("FOLIO_KEY")
    completadas = base.groupby(dimensiones,dropna=False,observed=True).agg(Completadas=("FOLIO_KEY","size")).reset_index()
    casos = reincidencias_df[reincidencias_df["ES_REINCIDENCIA"].eq("SI")].drop_duplicates("FOLIO_KEY")
    rein = casos.groupby(dimensiones,dropna=False,observed=True).agg(Reincidentes=("FOLIO_KEY","size")).reset_index()
    res = completadas.merge(rein,on=dimensiones,how="left")
    res["Reincidentes"] = res["Reincidentes"].fillna(0).astype(int)
    res["Reincidencia (%)"] = res["Reincidentes"].div(res["Completadas"].replace(0,np.nan)).mul(100)
    return res



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

# ==============================================================================
# CSS DE ALTO IMPACTO (COMPATIBLE CON STREAMLIT CLOUD Y LOCALHOST)
# ==============================================================================

def _headers_github_contenido() -> Dict[str, str]:
    """Headers estándar para llamadas a la API de contenidos/objetos de GitHub."""
    h = dict(HEADERS)
    h["Accept"] = "application/vnd.github.v3+json"
    return h








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
        claves = [columna_exacta(final, alias, False) for alias in aliases]
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
    # Confirmar la ruta & el contenido de la revisión escrita antes del mensaje verde.
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
        "sha": esperado,
        "filas": len(final),
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "url": f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/blob/{commit}/{quote(ruta, safe='/')}",
    }
    return final




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
    /* El enlace a GitHub es un elemento <a>, no un botón de st.button. */
    .st-key-formulario_carga [data-testid="stLinkButton"] a {
        background: #ffffff !important;
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        border: 1px solid #64748b !important;
        border-radius: 8px !important;
        opacity: 1 !important;
    }
    .st-key-formulario_carga [data-testid="stLinkButton"] a:hover {
        background: #e2e8f0 !important;
        border-color: #334155 !important;
    }
    .st-key-formulario_carga [data-testid="stLinkButton"] a:focus-visible {
        outline: 3px solid #007c83 !important;
        outline-offset: 2px;
    }
    /* Fondo uniforme incluso en el contenedor que rodea al icono del ojo. */
    .st-key-formulario_carga [data-testid="stTextInput"] div {
        background: #ffffff !important;
        color: #000000 !important;
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
    st.markdown("###  Pegar información y guardar en GitHub")
    st.caption(f"Versión {VERSION_SISTEMA} · Repositorio: {GITHUB_USER}/{GITHUB_REPO} · Rama: {GITHUB_BRANCH}")
    resultado_previo = st.session_state.get("resultado_captura")
    if isinstance(resultado_previo, dict):
        st.success(f"Último guardado verificado: {resultado_previo['ruta']} · {resultado_previo['filas']:,} registros · {resultado_previo['fecha']}")
        st.link_button("Abrir el CSV guardado en GitHub", resultado_previo["url"])
    # Sin columnas ni pestañas envolventes: ocupa todo el ancho del área principal.
    nueva_semana = st.checkbox("Iniciar nueva semana con un archivo nuevo", help="Integra la semana activa anterior al histórico y usa el nuevo archivo como semana activa.")
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
        tabla_operativa(df_preview.head(20), width="stretch", hide_index=True)

    st.markdown("#### Carpeta de destino")
    if st.button(" Actualizar listado de carpetas", key="actualizar_carpetas"):
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
            st.caption(f"La consulta operativa utiliza la carpeta {GITHUB_FOLDER}; este CSV se guardará en el destino que elegiste.")
    clave = st.text_input("Clave de autorización del portal", type="password", key="token_auth_carga_v2")
    puede_guardar = df_preview is not None and carpeta is not None and bool(nombre_csv)
    if st.button(" Guardar CSV en la carpeta seleccionada", type="primary", disabled=not puede_guardar, key="guardar_captura"):
        st.session_state.pop("resultado_captura", None)
        if not GITHUB_TOKEN:
            st.error("Configura github.token en los secretos de Streamlit para guardar en el repositorio.")
            return
        if clave != st.secrets.get("UPLOAD_PASSWORD", None):
            st.error("Clave de autorización incorrecta.")
            return
        with st.spinner("Guardando el CSV en la carpeta seleccionada..."):
            try:
                final = guardar_csv_en_carpeta(destino, df_preview, agregar)
            except Exception as exc:
                st.error(f"No se confirmó el guardado: {exc}")
                return
        mensaje = f" Guardado y verificado en GitHub: {destino} · {len(final):,} registros en el CSV."
        confirmado = final.attrs["guardado_github"]
        st.session_state["resultado_captura"] = confirmado
        st.success(mensaje)
        st.link_button("Abrir el CSV guardado en GitHub", confirmado["url"])
        inventario_repositorio_github.clear()
        snapshot_consulta.clear()
        # El consolidado pertenece únicamente a la carpeta configurada del dashboard.
        if carpeta == GITHUB_FOLDER.strip("/"):
            try:
                actualizar_particiones(nombre_csv, final, nueva_semana)
                st.success("La base de consulta también quedó actualizada.")
            except Exception as exc:
                st.warning(f"El CSV quedó guardado; la base de consulta no se actualizó: {exc}")





def inyectar_estilos_base_ui():
    st.html('''<style>
    .stApp, .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button,
    .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp select {font-family:Arial,Helvetica,sans-serif!important;}
    .stApp {background:#f5f8fb;color:#122a43;}
    .stApp h1,.stApp h2,.stApp h3,.stApp h4, .stApp [data-testid="stMarkdownContainer"] {color:#16324f;}
    [data-baseweb="select"] svg {fill:#16324f!important;}
    [data-testid="stSelectbox"] [role="group"], [data-testid="stMultiSelect"] [role="group"],
    [data-testid="stDateInput"] [role="group"], [data-testid="stSelectbox"] input,
    [data-testid="stMultiSelect"] input, [data-testid="stDateInput"] input,
    [data-testid="stSelectbox"] button, [data-testid="stMultiSelect"] button {background:#fff!important;color:#16324f!important;}
    [role="dialog"] {background:#f5f8fb;color:#16324f;}
    [data-testid="stMainMenu"], [data-testid="stAppDeployButton"] {display:none;}
    [data-testid="stDownloadButton"] button {background:#fff!important;color:#16324f!important;border:1px solid #b5c5d6!important;}
    [data-testid="stHeader"] {background:#f5f8fb;}
    [data-testid="stSidebar"] {background:#eaf0f6;border-right:1px solid #cbd5e1;}
    [data-testid="stSidebar"] * {color:#16324f;}
    h1 {font-weight:900!important;letter-spacing:-.04em;} h2,h3 {font-weight:700!important;letter-spacing:-.02em;}
    [data-testid="stWidgetLabel"] p {color:#16324f!important;font-weight:600;}
    [data-baseweb="select"]>div, [data-baseweb="input"], [data-baseweb="base-input"], textarea,
    [role="listbox"], [role="option"] {background:#fff!important;color:#16324f!important;}
    [data-baseweb="select"] input, [data-baseweb="select"] span, input {color:#16324f!important;}
    [data-testid="stButton"] button, [data-testid="stLinkButton"] a {border-radius:9px;background:#fff;color:#16324f;border:1px solid #b5c5d6;font-weight:700;}
    [data-testid="stButton"] button[kind="primary"] {background:#1e3e62;color:white;border-color:#1e3e62;}
    .encabezado {background:linear-gradient(120deg,#0b192c,#1e3e62);padding:30px;border-radius:16px;border-bottom:4px solid #00d2c8;margin:0 0 24px;}
    .encabezado h1 {color:#fff!important;margin:0;font-size:30px;}
    .encabezado p {color:#a7e9e5!important;font-weight:300;margin:8px 0 0;}
    .tarjetas {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:18px 0 24px;}
    .tarjeta {background:#fff;border:1px solid #dce5ed;border-top:4px solid #00b3ad;border-radius:12px;padding:20px;}
    .tarjeta .nombre {font-size:12px;font-weight:700;color:#45617b;text-transform:uppercase;letter-spacing:.06em;}
    .tarjeta .valor {font-size:34px;font-weight:900;color:#1e3e62;line-height:1.3;}
    .tarjeta:last-child {border-top-color:#f97316;}
    .tabla-contenedor {overflow:auto;border:1px solid #d2deea;border-radius:12px;margin:10px 0 18px;max-height:520px;}
    .tabla-operativa {font-family:Arial,Helvetica,sans-serif;width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap;}
    .tabla-operativa th {background:#1e3e62;color:white;padding:12px 16px;text-align:left;font-weight:700;position:sticky;top:0;border-bottom:3px solid #00d2c8;}
    .tabla-operativa td {padding:11px 16px;border-bottom:1px solid #dce5ed;color:#16324f;background:#fff;}
    .tabla-operativa tbody tr:nth-child(even) td {background:#eff8f8;}
    .tabla-operativa tbody tr:hover td {background:#d9efee;}
    @media(max-width:700px) {.encabezado {padding:20px;} .encabezado h1 {font-size:23px;} .tarjetas {grid-template-columns:1fr;gap:10px;} .tarjeta {padding:14px;} .tarjeta .valor {font-size:28px;}}
    </style>''')


def inyectar_estilos_css_enterprise():
    inyectar_estilos_base_ui()


_TABLA_CONTADOR = 0


def tabla_operativa(data, **kwargs):
    global _TABLA_CONTADOR
    _TABLA_CONTADOR += 1
    df = data.data if hasattr(data, "data") and not isinstance(data, pd.DataFrame) else data
    df = df.copy()
    df = df.rename(columns={"FECHA_TRUNCADA":"Día de cierre","SEMANA_DIM":"Semana de cierre","MES_DIM":"Mes de cierre","AÑO_DIM":"Año de cierre","Nombre_Poliza":"Póliza","Usuario_Tecnico":"Técnico","Cuenta_Cliente":"Cuenta"})
    if df.empty:
        st.info("Sin registros para la selección."); return
    if len(df) > 50:
        pagina = st.number_input("Página de resultados", min_value=1, max_value=(len(df)+49)//50, value=1, step=1, key=f"pagina_tabla_{_TABLA_CONTADOR}")
        df = df.iloc[(pagina-1)*50:pagina*50]
        st.caption(f"{len(data):,} filas · 50 filas por página")
    html = df.to_html(index=False, classes="tabla-operativa", border=0, escape=True, na_rep="", float_format=lambda x: f"{x:,.2f}")
    st.html('<div class="tabla-contenedor">' + html + '</div>')


def grafica_operativa(fig, key):
    fig.update_layout(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                      font=dict(family="Arial, Helvetica, sans-serif", color="#16324f", size=12),
                      colorway=["#1e3e62", "#00b3ad", "#f97316", "#10b981"], dragmode=False,
                      margin=dict(l=30,r=30,t=65,b=60), legend=dict(orientation="h",y=-.2,x=0),
                      title_font=dict(family="Arial",color="#16324f",size=18))
    fig.update_xaxes(fixedrange=True, gridcolor="#e3ebf2")
    fig.update_yaxes(fixedrange=True, gridcolor="#e3ebf2")
    if "yaxis2" in fig.layout:
        fig.update_layout(yaxis2=dict(fixedrange=True))
    st.plotly_chart(fig, width="stretch", key=key, theme=None,
                    config={"displayModeBar":False,"scrollZoom":False,"doubleClick":False,"responsive":True,"locale":"es"})
    if isinstance(fig.layout.meta, dict) and fig.layout.meta.get("leyenda_externa"):
        leyenda = ''.join(f'<span style="display:inline-flex;align-items:center;gap:6px"><i style="display:inline-block;width:10px;height:10px;background:{tr.marker.color}"></i>{html.escape(str(tr.name))}</span>' for tr in fig.data)
        st.html('<div style="font:12px Arial;color:#16324f;display:flex;gap:10px;flex-wrap:wrap;margin:0 0 12px"><b>Causa del servicio anterior</b>'+leyenda+'</div>')


def tarjetas_indicadores(df):
    resumen = resumen_indicadores(df).iloc[0]
    tasa = resumen["Reincidencia (%)"]
    valores = [("Órdenes completadas", f"{int(resumen['Completadas']):,}"),
               ("Soportes reincidentes", f"{int(resumen['Reincidentes']):,}"),
               ("Tasa de reincidencia", "Sin base" if pd.isna(tasa) else f"{tasa:.2f}%")]
    st.html('<div class="tarjetas">'+''.join(f'<div class="tarjeta"><div class="nombre">{n}</div><div class="valor">{v}</div></div>' for n,v in valores)+'</div>')
    st.caption("Reincidentes ÷ órdenes completadas de instalación, soporte, cambio de domicilio y cambio de equipo. Se aplica la misma selección a ambos valores.")


def evolucion_cierre(df, dimension):
    resumen = resumen_indicadores(df, [dimension]).sort_values(dimension)
    fig = go.Figure()
    fig.add_bar(x=resumen[dimension], y=resumen["Completadas"], name="Completadas", marker_color="#1e3e62")
    fig.add_scatter(x=resumen[dimension],y=resumen["Reincidentes"],name="Reincidentes",mode="lines+markers",line_color="#00b3ad")
    if len(resumen)>1:
        fig.add_scatter(x=resumen[dimension],y=calcular_tendencia_lineal_robusta(resumen["Completadas"].tolist()),name="Tendencia de completadas",line=dict(color="#f97316",dash="dash"))
    base = df[df["Orden_Completa"] & df["Usuario_ID"].ne("")]
    actividad = base.groupby(dimension).agg(tecnicos=("Usuario_ID","nunique"),dias=("FECHA_TRUNCADA","nunique"),ordenes=("FOLIO_KEY","size"))
    productividad = actividad["ordenes"].div(actividad["tecnicos"] * actividad["dias"]).reindex(resumen[dimension]).fillna(0)
    fig.add_scatter(x=resumen[dimension],y=productividad,name="Órdenes por técnico y día",yaxis="y2",line=dict(color="#10b981"))
    if len(productividad)>1:
        fig.add_scatter(x=resumen[dimension],y=calcular_tendencia_lineal_robusta(productividad.tolist()),
                        name="Tendencia de productividad",yaxis="y2",
                        line=dict(color="#668bb1",dash="dot"))
    fig.update_layout(title="Cierres, reincidencias y productividad",xaxis_title="Periodo de cierre",yaxis_title="Órdenes",
                      yaxis2=dict(title="Órdenes por técnico y día",overlaying="y",side="right",showgrid=False))
    return fig


def renderizar_pestana_polizas_cuadrillas(df, dimension):
    seccion = st.radio("Vista", ["Evolución", "Pólizas", "Técnicos", "Reportes"], horizontal=True, key="vista_operativa")
    # Actividad operativa considera todo el catálogo completado. Los cuatro tipos
    # evaluables se reservan exclusivamente para la tasa de reincidencia.
    base = df[df["Orden_Completa"]].drop_duplicates("FOLIO_KEY")
    if seccion == "Evolución":
        resumen = resumen_indicadores(df,[dimension])
        activos = base[base["Usuario_ID"].ne("")].groupby(dimension).agg(Técnicos=("Usuario_ID","nunique"),Días=("FECHA_TRUNCADA","nunique"))
        resumen = resumen.merge(activos,on=dimension,how="left")
        resumen["Órdenes por técnico y día"] = resumen["Completadas"].div(resumen["Técnicos"]*resumen["Días"])
        panel_grafico(evolucion_cierre(df,dimension),resumen,"evolucion_cierres")
        selector_detalle(df,df,"Usuario_Tecnico","Técnico","evolucion")
        st.subheader("Técnicos activos por empresa y periodo")
        matriz = base[base["Usuario_ID"].ne("")].pivot_table(index="Empresa",columns=dimension,values="Usuario_ID",aggfunc="nunique",fill_value=0).reset_index()
        periodos = [c for c in matriz.columns if c != "Empresa"]
        if periodos:
            valores = matriz[periodos].to_numpy(dtype=float)
            matriz.insert(1,"Tendencia",[" → ".join(f"{int(v):,}" for v in fila) for fila in valores])
            matriz["Promedio por periodo"] = np.round(valores.mean(axis=1),1)
            total = {"Empresa":"TOTAL GENERAL","Tendencia":" → ".join(f"{int(v):,}" for v in valores.sum(axis=0)),
                     "Promedio por periodo":round(float(valores.sum(axis=0).mean()),1)}
            total.update({p:int(matriz[p].sum()) for p in periodos})
            matriz = pd.concat([matriz,pd.DataFrame([total])],ignore_index=True)
        tabla_operativa(matriz)
        st.subheader("Distribución del trabajo completado")
        c1, c2 = st.columns(2)
        por_poliza = base.groupby("Nombre_Poliza",observed=True).agg(Órdenes=("FOLIO_KEY","size")).reset_index().sort_values("Órdenes")
        por_tipo = base.groupby("Tipo_Orden",observed=True).agg(Órdenes=("FOLIO_KEY","size")).reset_index().sort_values("Órdenes").tail(10)
        with c1:
            fig_poliza = px.bar(por_poliza,x="Órdenes",y="Nombre_Poliza",orientation="h",title="Volumen por póliza",color_discrete_sequence=["#1e3e62"])
            panel_grafico(fig_poliza,por_poliza.rename(columns={"Nombre_Poliza":"Póliza"}),"volumen_poliza",indice_vista=1)
        with c2:
            fig_tipo = px.bar(por_tipo,x="Órdenes",y="Tipo_Orden",orientation="h",title="Diez tipos de servicio con mayor volumen",color_discrete_sequence=["#00b3ad"])
            panel_grafico(fig_tipo,por_tipo.rename(columns={"Tipo_Orden":"Tipo de servicio"}),"volumen_tipo",indice_vista=1)
    elif seccion in ("Pólizas","Técnicos"):
        dims = ["Nombre_Poliza","Empresa"] if seccion=="Pólizas" else ["Usuario_Tecnico","Empresa"]
        origen = base[base["Usuario_ID"].ne("")] if seccion == "Técnicos" else base
        tabla = origen.groupby(dims,observed=True,dropna=False).agg(
            Completadas=("FOLIO_KEY","size"),Técnicos=("Usuario_ID","nunique"),
            Días_operativos=("FECHA_TRUNCADA","nunique"),Reincidentes=("ES_REINCIDENCIA",lambda s:int(s.eq("SI").sum()))
        ).reset_index()
        evaluada = origen[origen["Tipo_Orden"].isin(TIPOS_EVALUABLES)].groupby(dims,observed=True,dropna=False).size().reset_index(name="Base evaluada")
        tabla = tabla.merge(evaluada,on=dims,how="left")
        tabla["Base evaluada"] = tabla["Base evaluada"].fillna(0).astype(int)
        tabla["Productividad diaria"] = tabla["Completadas"].div((tabla["Técnicos"].clip(lower=1))*tabla["Días_operativos"].clip(lower=1)).round(2)
        tabla["Reincidencia (%)"] = tabla["Reincidentes"].div(tabla["Base evaluada"].replace(0,np.nan)).mul(100).round(2)
        tabla = tabla.sort_values("Productividad diaria" if seccion=="Técnicos" else "Completadas",ascending=False)
        if tabla.empty:
            st.info("Sin órdenes completadas para esta vista."); return
        titulo = "Productividad diaria por técnico" if seccion=="Técnicos" else "Órdenes completadas por póliza"
        medida = "Productividad diaria" if seccion=="Técnicos" else "Completadas"
        fig = px.bar(tabla.head(20),x=medida,y=dims[0],color="Empresa",orientation="h",title=titulo,color_discrete_sequence=["#1e3e62","#00b3ad","#f97316","#10b981"])
        panel_grafico(fig,tabla.rename(columns={"Nombre_Poliza":"Póliza","Usuario_Tecnico":"Técnico"}),"distribucion_operativa")
        selector_detalle(origen,df,dims[0],"Póliza" if seccion == "Pólizas" else "Técnico","distribucion")
    else:
        st.subheader("Reporte de la selección")
        st.caption(f"{len(df):,} órdenes en el periodo y filtros seleccionados.")
        if st.button("Preparar archivo CSV"):
            st.download_button("Descargar reporte",df.to_csv(index=False).encode("utf-8-sig"),"reporte_operativo.csv","text/csv",on_click="ignore")


@st.dialog("Detalle operativo", width="large")
def detalle_operativo(df, titulo):
    st.subheader(titulo)
    st.caption("Historial de la entidad seleccionada. Puede incluir antecedentes fuera del periodo del reporte para explicar las reincidencias.")
    cols = {"Cuenta_Cliente":"Cuenta","Usuario_Tecnico":"Técnico","Tipo_Orden":"Servicio","_datetime_termino":"Cierre",
            "Empresa":"Empresa","ES_REINCIDENCIA":"Reincidencia","Semana_Origen_Reincidencia":"Semana del antecedente",
            "Dias_Entre_Visitas":"Días transcurridos","TIPO_2":"Causa anterior","Falla_Nueva":"Falla actual"}
    vista = df.sort_values("_datetime_termino")[list(cols)].rename(columns=cols)
    tabla_operativa(vista)
    if st.button("Preparar descarga del detalle",key="preparar_detalle"):
        st.download_button("Descargar detalle CSV",vista.to_csv(index=False).encode("utf-8-sig"),"detalle_operativo.csv","text/csv",on_click="ignore",key="descargar_detalle")


def panel_grafico(fig, datos, clave, indice_vista=0):
    vista = st.radio("Presentación",["Gráfica y tabla","Gráfica","Tabla"],horizontal=True,index=indice_vista,key=f"vista_{clave}")
    if vista != "Tabla":
        grafica_operativa(fig,clave)
    if vista != "Gráfica":
        tabla_operativa(datos)
    if st.button("Preparar descarga de esta vista",key=f"preparar_{clave}"):
        st.download_button("Descargar datos CSV",datos.to_csv(index=False).encode("utf-8-sig"),f"{clave}.csv","text/csv",key=f"descarga_{clave}",on_click="ignore")


def selector_detalle(filtrado, completo, campo, etiqueta, clave):
    opciones = sorted(filtrado[campo].dropna().unique())
    if campo == "Cuenta_Cliente" and len(opciones) > 100:
        opciones = filtrado.loc[filtrado["ES_REINCIDENCIA"].eq("SI"),campo].value_counts().head(100).index.tolist()
        if not opciones:
            opciones = sorted(filtrado[campo].dropna().unique())[:100]
        st.caption("Selector limitado a 100 cuentas. Usa la búsqueda exacta para localizar cualquier otra cuenta sin cargar todo el catálogo.")
    if not opciones:
        return
    seleccionado = st.selectbox(etiqueta,opciones,key=f"entidad_{clave}")
    if st.button("Abrir detalle",key=f"detalle_{clave}"):
        detalle_operativo(completo[completo[campo].eq(seleccionado)],f"{etiqueta}: {seleccionado}")


def datos_linea_tiempo(df, dimension):
    casos = df[df["ES_REINCIDENCIA"].eq("SI")].copy()
    casos["Causa del antecedente"] = [obtener_valor_tipo2(c, t) for c, t in zip(casos["Causa_Origen"], casos["Tipo_Anterior"])]
    casos["Causa del antecedente"] = casos["Causa del antecedente"].map(lambda x: re.sub(r"\s+", " ", reparar_codificacion(x)).strip())
    return casos.groupby([dimension,"Causa del antecedente"],observed=True).agg(Reincidencias=("FOLIO_KEY","nunique")).reset_index().sort_values(dimension)


def linea_tiempo_reincidencias(df, dimension, clave):
    resumen = datos_linea_tiempo(df, dimension)
    fig = px.bar(resumen, x=dimension, y="Reincidencias", color="Causa del antecedente", barmode="stack",
                 title="Reincidencias por periodo", color_discrete_sequence=["#1e3e62","#00b3ad","#f97316","#10b981","#668bb1","#d08b38"],
                 labels={dimension:"Periodo de cierre"})
    fig.update_traces(width=.18)
    fig.update_layout(height=240,bargap=.8,showlegend=False,xaxis_title=None,meta={"leyenda_externa":True})
    panel_grafico(fig,resumen,clave,indice_vista=1)


def renderizar_pestana_reincidencias_total(df, dimension, completo=None):
    completo = df if completo is None else completo
    st.subheader("Reincidencias a 60 días")
    agrupacion = st.selectbox("Desglose de la tasa", [dimension,"Empresa","Distrito","Nombre_Poliza","Usuario_Tecnico"],
                             format_func=lambda x:{"Nombre_Poliza":"Póliza","Usuario_Tecnico":"Técnico",dimension:"Periodo"}.get(x,x))
    tabla_operativa(resumen_indicadores(df,[agrupacion]).rename(columns={"Nombre_Poliza":"Póliza","Usuario_Tecnico":"Técnico"}))
    st.caption("La tasa se atribuye a la orden actual. El detalle conserva la semana, empresa y técnico de su antecedente.")

    casos_base = df[df["ES_REINCIDENCIA"].eq("SI")].copy()
    with st.expander("Filtros del servicio anterior", expanded=False):
        st.caption("Estos filtros afinan las reincidencias mostradas; el denominador conserva las órdenes completadas de los filtros generales.")
        c1, c2 = st.columns(2)
        with c1:
            empresas_previas = st.multiselect("Empresa anterior",sorted(casos_base["Empresa_Origen_Reincidencia"].replace("",np.nan).dropna().unique()),key="rein_empresa_anterior")
            tecnicos_previos = st.multiselect("Técnico anterior",sorted(casos_base["Usuario_Origen_Reincidencia"].replace("",np.nan).dropna().unique()),key="rein_tecnico_anterior")
        with c2:
            causas_previas = st.multiselect("Causa o tipo anterior",sorted(casos_base["TIPO_2"].replace("",np.nan).dropna().unique()),key="rein_causa_anterior")
            fallas_actuales = st.multiselect("Falla del soporte actual",sorted(casos_base["Falla_Nueva"].replace("",np.nan).dropna().unique()),key="rein_falla_actual")
    filtros_antecedente = {
        "Empresa_Origen_Reincidencia": empresas_previas,
        "Usuario_Origen_Reincidencia": tecnicos_previos,
        "TIPO_2": causas_previas,
        "Falla_Nueva": fallas_actuales,
    }
    casos_filtrados = casos_base
    for campo, valores in filtros_antecedente.items():
        if valores:
            casos_filtrados = casos_filtrados[casos_filtrados[campo].isin(valores)]
    hay_filtro_antecedente = any(filtros_antecedente.values())
    alcance_reincidencias = pd.concat([df[df["ES_REINCIDENCIA"].ne("SI")],casos_filtrados],ignore_index=True) if hay_filtro_antecedente else df

    st.subheader("Reincidencias por técnico")
    linea_tiempo_reincidencias(alcance_reincidencias,dimension,"tiempo_tecnico")
    base_tecnicos = df[df["Usuario_ID"].ne("")]
    tecnicos = resumen_con_reincidencias_filtradas(base_tecnicos,casos_filtrados,["Usuario_Tecnico"]).rename(columns={"Usuario_Tecnico":"Técnico"}).sort_values("Reincidentes",ascending=False)
    atribucion = st.radio("Atribución del técnico",["Atención actual","Servicio anterior"],horizontal=True,key="atribucion_tecnico")
    campo_tecnico = "Usuario_Tecnico"
    if atribucion == "Servicio anterior":
        tecnicos = casos_filtrados.groupby(["Usuario_Origen_Reincidencia","Empresa_Origen_Reincidencia"],observed=True).agg(
            Reincidentes=("FOLIO_KEY","nunique"),Causas=("TIPO_2",lambda s:" | ".join(sorted(set(map(str,s.dropna()))))),
            Fallas=("Falla_Nueva",lambda s:" | ".join(sorted(set(map(str,s.dropna())))))
        ).reset_index().rename(columns={"Usuario_Origen_Reincidencia":"Técnico","Empresa_Origen_Reincidencia":"Empresa"}).sort_values("Reincidentes",ascending=False)
        st.caption("Antecedentes vinculados a soportes del periodo. Esta atribución no modifica la tasa de las tarjetas ni utiliza un denominador de otro periodo.")
        campo_tecnico = "Usuario_Origen_Reincidencia"
    fig = px.bar(tecnicos.head(20),x="Reincidentes",y="Técnico",orientation="h",title="Soportes reincidentes por técnico",color_discrete_sequence=["#1e3e62"])
    panel_grafico(fig,tecnicos,"reincidencias_tecnico")
    if atribucion == "Servicio anterior":
        opciones = tecnicos["Técnico"].tolist()
        if opciones:
            elegido = st.selectbox("Técnico del servicio anterior",opciones,key="tecnico_anterior_detalle")
            if st.button("Abrir detalle",key="detalle_origen"):
                detalle_operativo(completo[completo["Usuario_Tecnico"].eq(elegido) | completo["Usuario_Origen_Reincidencia"].eq(elegido)],elegido)
    else:
        selector_detalle(df[df["Usuario_ID"].ne("")],completo,"Usuario_Tecnico","Técnico","rein_usuario")
    st.subheader("Reincidencias por cuenta")
    cuenta = st.text_input("Buscar cuenta exacta",key="cuenta_reincidente").strip().upper()
    alcance = alcance_reincidencias[alcance_reincidencias["Cuenta_Cliente"].eq(cuenta)] if cuenta else alcance_reincidencias
    base_alcance = df[df["Cuenta_Cliente"].eq(cuenta)] if cuenta else df
    linea_tiempo_reincidencias(alcance,dimension,"tiempo_cuenta")
    rein_alcance = casos_filtrados[casos_filtrados["Cuenta_Cliente"].eq(cuenta)] if cuenta else casos_filtrados
    cuentas = resumen_con_reincidencias_filtradas(base_alcance,rein_alcance,["Cuenta_Cliente"])
    detalle_cuentas = rein_alcance.groupby("Cuenta_Cliente",observed=True).agg(
        Semanas=("Semana_Origen_Reincidencia",lambda s:" | ".join(sorted(set(map(str,s.dropna()))))),
        Causas=("TIPO_2",lambda s:" | ".join(sorted(set(map(str,s.dropna()))))),
        Fallas=("Falla_Nueva",lambda s:" | ".join(sorted(set(map(str,s.dropna()))))),
        Técnicos_anteriores=("Usuario_Origen_Reincidencia",lambda s:" | ".join(sorted(set(map(str,s.dropna())))))
    ).reset_index()
    cuentas = cuentas.merge(detalle_cuentas,on="Cuenta_Cliente",how="left").rename(columns={"Cuenta_Cliente":"Cuenta"}).sort_values("Reincidentes",ascending=False)
    fig = px.bar(cuentas.head(20),x="Reincidentes",y="Cuenta",orientation="h",title="Soportes reincidentes por cuenta",color_discrete_sequence=["#00b3ad"])
    fig.update_yaxes(type="category")
    panel_grafico(fig,cuentas,"reincidencias_cuenta")
    st.caption("Las gráficas muestran hasta 20 entidades; las tablas y descargas incluyen toda la selección.")
    selector_detalle(alcance,completo,"Cuenta_Cliente","Cuenta","rein_cuenta")
    rein = alcance[alcance["ES_REINCIDENCIA"].eq("SI")]
    st.subheader("Antecedentes identificados")
    cols = {"Cuenta_Cliente":"Cuenta","FOLIO_KEY":"Orden actual","_datetime_termino":"Cierre actual",
            "Tipo_Anterior":"Servicio anterior","Fecha_Cierre_Anterior":"Cierre anterior","Dias_Entre_Visitas":"Días transcurridos",
            "Semana_Origen_Reincidencia":"Semana del antecedente","Usuario_Origen_Reincidencia":"Técnico anterior",
            "Empresa_Origen_Reincidencia":"Empresa anterior","TIPO_2":"Causa anterior","Falla_Nueva":"Falla actual"}
    tabla_operativa(rein[list(cols)].rename(columns=cols))


def filtrar_consulta(df, desde=None, hasta=None, selecciones=None):
    resultado = df
    if desde is not None:
        resultado = resultado[resultado["_datetime_termino"] >= pd.Timestamp(desde)]
    if hasta is not None:
        resultado = resultado[resultado["_datetime_termino"] < pd.Timestamp(hasta)+pd.Timedelta(days=1)]
    for col, valores in (selecciones or {}).items():
        if valores:
            resultado = resultado[resultado[col].isin(valores)]
    return resultado


def main():
    global _TABLA_CONTADOR
    _TABLA_CONTADOR = 0
    inyectar_estilos_base_ui()
    st.html('<div class="encabezado"><h1>Operaciones · Norte La Baja</h1><p>Cierres, productividad y calidad del servicio</p></div>')
    st.sidebar.title("Operaciones")
    seccion = st.sidebar.radio("Sección", ["Consulta operativa","Captura de información","Administración de datos"],key="seccion_v15")
    st.sidebar.caption(f"Versión {VERSION_SISTEMA}")
    if st.sidebar.button("Actualizar datos"):
        snapshot_consulta.clear()
        st.rerun()
    if seccion == "Captura de información":
        renderizar_modulo_carga_github(); return
    if seccion == "Administración de datos":
        administrar_base(); return
    try:
        with st.spinner("Leyendo la base de consulta..."):
            df = ejecutar_pipeline_ingestion_datos()
    except BaseNoPreparada as exc:
        st.info(str(exc)); return
    except Exception as exc:
        st.error(str(exc))
        st.info("Abre Administración de datos para preparar el histórico y la semana activa."); return
    st.caption(f"{len(df):,} órdenes únicas · Dos archivos de consulta · Preparación inicial: {df.attrs.get('segundos_preparacion',0):.2f} s; reutilizada mientras no cambien las fuentes.")
    fechas = df["_datetime_termino"].dropna()
    if fechas.empty:
        st.error("No se encontraron fechas de cierre válidas."); return
    st.sidebar.subheader("Periodo de cierre")
    periodo = st.sidebar.date_input("Desde y hasta",(fechas.min().date(),fechas.max().date()),format="DD/MM/YYYY")
    if len(periodo)!=2:
        st.info("Selecciona ambas fechas del periodo."); return
    dimension = st.sidebar.selectbox("Agrupar por",["FECHA_TRUNCADA","SEMANA_DIM","MES_DIM","AÑO_DIM"],index=1,
                   format_func=lambda x:{"FECHA_TRUNCADA":"Día","SEMANA_DIM":"Semana","MES_DIM":"Mes","AÑO_DIM":"Año"}[x])
    selecciones = {}
    catalogo_filtrado = filtrar_consulta(df,*periodo,{})
    for col, etiqueta in [("AÑO_DIM","Año"),("MES_DIM","Mes"),("SEMANA_DIM","Semana"),("Empresa","Empresa"),
                          ("Distrito","Distrito"),("Nombre_Poliza","Póliza"),("Usuario_Tecnico","Técnico"),("Tipo_Orden","Tipo de servicio")]:
        opciones = sorted(catalogo_filtrado[col].dropna().astype(str).unique())
        selecciones[col] = st.sidebar.multiselect(etiqueta,opciones,placeholder="Todos",key=f"filtro_v15_{col}")
        if selecciones[col]:
            catalogo_filtrado = catalogo_filtrado[catalogo_filtrado[col].isin(selecciones[col])]
    filtrado = filtrar_consulta(df,*periodo,selecciones)
    tarjetas_indicadores(filtrado)
    sin_usuario = int(df["Usuario_ID"].eq("").sum())
    if sin_usuario:
        st.caption(f"{sin_usuario:,} órdenes sin usuario identificado. Se conservan en los totales; no se atribuyen a un técnico individual.")
    sin_fecha = int(df["_datetime_termino"].isna().sum())
    ambiguas = int(filtrado["Revision_Cronologia"].ne("").sum())
    if sin_fecha or ambiguas:
        st.caption(f"Control de calidad: {sin_fecha:,} órdenes sin cierre válido en la base; {ambiguas:,} secuencias con cierres simultáneos en la selección. No se infieren antecedentes en estos casos.")
    apartado = st.radio("Análisis",["Actividad","Reincidencias","Cambios de equipo","Causas y soluciones"],horizontal=True,key="analisis_v15")
    if apartado=="Actividad":
        renderizar_pestana_polizas_cuadrillas(filtrado,dimension)
    elif apartado=="Reincidencias":
        renderizar_pestana_reincidencias_total(filtrado,dimension,df)
    else:
        st.info("Sección pendiente de definición. La base de consulta y los filtros ya están disponibles.")


if __name__ == "__main__":
    main()
