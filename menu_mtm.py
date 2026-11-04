"""
Menú de cálculo de MtM de Opciones FX (Streamlit), cliente de la API (api.py).

Opciones del menú:
  1. Valorización MtM       -> POST /fx-options/mtm
  2. Payoff vencimientos    -> POST /fx-options/payoff
  3. Proceso diario         -> POST /fx-options/proceso-diario
  4. Informes generados     -> GET  /fx-options/salidas

Uso (con la API corriendo):
  streamlit run menu_mtm.py --server.port 8502
Variables de entorno:
  FXO_API_URL   URL de la API (por defecto http://127.0.0.1:8001); puede ser
                la de Heroku.
  FXO_API_KEY   clave de la API, si la API la exige.
  FXO_DATA      carpeta con los insumos para la opción "Carpeta data/"
                (por defecto data/ de este repositorio).
"""
from __future__ import annotations

import io
import os
import re
from datetime import date

import pandas as pd
import requests
import streamlit as st

API_URL = os.environ.get("FXO_API_URL", "http://127.0.0.1:8001").rstrip("/")
API_KEY = os.environ.get("FXO_API_KEY")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}
BASE = os.path.dirname(os.path.abspath(__file__))
DIR_DATA = os.environ.get("FXO_DATA", os.path.join(BASE, "data"))
LOGO = os.path.join(BASE, "assets", "logo.png")
PAT_OPS = re.compile(r"operaciones diarias.*al (\d{4})_(\d{2})_(\d{2})\.xlsx$", re.I)

OPCIONES = {
    "Valorización MtM": "mtm",
    "Payoff vencimientos": "payoff",
    "Proceso diario (informes)": "proceso",
    "Informes generados": "salidas",
}

# Columnas a mostrar: clave del JSON -> título
COLS_MTM = {
    "folio": "Folio", "fecha_inicio": "Fecha Inicio",
    "fecha_vencimiento": "Fecha Vencimiento", "dias_al_vencimiento": "Días al Venc.",
    "contraparte": "Contraparte", "lado": "Lado Nevasa", "tipo": "Tipo",
    "nominal": "Monto Principal", "strike": "Strike", "forward": "Forward",
    "volatilidad": "Volatilidad (%)", "prima_clp": "Prima (CLP)",
    "prima_usd": "Prima (USD)", "mtm_clp": "MtM (CLP)",
    "pl_no_realizado_clp": "P&L No Realizado (CLP)",
    "pl_no_realizado_usd": "P&L No Realizado (USD)",
}
COLS_PAYOFF = {
    "folio": "Folio", "fecha_inicio": "Fecha Inicio",
    "fecha_vencimiento": "Fecha Vencimiento", "contraparte": "Contraparte",
    "lado": "Lado Nevasa", "tipo": "Tipo", "nominal": "Monto Principal",
    "strike": "Strike", "fixing": "Fixing", "fuente_fixing": "Fuente Fixing",
    "itm": "ITM", "prima_clp": "Prima (CLP)", "prima_usd": "Prima (USD)",
    "payoff_clp": "Payoff (CLP)", "pl_clp": "P&L Nevasa (CLP)",
}


# --------------------------------------------------------------------------
# Comunicación con la API
# --------------------------------------------------------------------------
def _api_ok():
    try:
        return requests.get(f"{API_URL}/fx-options/salud", timeout=3).ok
    except requests.RequestException:
        return False


def _post(ruta, insumos):
    (n_grid, b_grid), (n_ops, b_ops), fecha = insumos
    files = {
        "grid": (n_grid, b_grid, "application/octet-stream"),
        "operaciones": (n_ops, b_ops, "application/octet-stream"),
    }
    data = {"fecha_proceso": fecha.isoformat()} if fecha else {}
    try:
        r = requests.post(f"{API_URL}{ruta}", files=files, data=data,
                          headers=HEADERS, timeout=300)
    except requests.RequestException as e:
        st.error(f"No se pudo conectar con la API en {API_URL}: {e}")
        return None
    if not r.ok:
        try:
            detalle = r.json().get("detail", r.text)
        except ValueError:
            detalle = r.text
        st.error(f"Error {r.status_code}: {detalle}")
        return None
    return r.json()


# --------------------------------------------------------------------------
# Insumos (barra lateral)
# --------------------------------------------------------------------------
def _fecha_de(nombre):
    m = PAT_OPS.search(nombre)
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _insumos():
    """Devuelve ((nombre_grid, bytes), (nombre_ops, bytes), fecha|None) o None."""
    st.sidebar.subheader("Insumos")
    origen = st.sidebar.radio("Origen de los archivos",
                              ["Carpeta data/", "Subir archivos"], horizontal=True)
    if origen == "Carpeta data/":
        archivos = sorted(os.listdir(DIR_DATA)) if os.path.isdir(DIR_DATA) else []
        grids = [a for a in archivos if a.lower().startswith("grid") and a.endswith(".xlsx")]
        ops = sorted([a for a in archivos if PAT_OPS.search(a)],
                     key=lambda a: _fecha_de(a), reverse=True)
        if not grids or not ops:
            st.sidebar.warning(f"No hay grid u operaciones en {DIR_DATA}.")
            return None
        n_grid = st.sidebar.selectbox("Datos de mercado", grids)
        n_ops = st.sidebar.selectbox("Cartera", ops)
        with open(os.path.join(DIR_DATA, n_grid), "rb") as f:
            grid = (n_grid, f.read())
        with open(os.path.join(DIR_DATA, n_ops), "rb") as f:
            cartera = (n_ops, f.read())
    else:
        up_grid = st.sidebar.file_uploader("Grid Vol Bloomberg Creasys.xlsx", type="xlsx")
        up_ops = st.sidebar.file_uploader("Operaciones Diarias ... .xlsx", type="xlsx")
        if not (up_grid and up_ops):
            st.sidebar.info("Sube los dos archivos del correo.")
            return None
        grid = (up_grid.name, up_grid.getvalue())
        cartera = (up_ops.name, up_ops.getvalue())

    sugerida = _fecha_de(cartera[0])
    usar_nombre = st.sidebar.checkbox(
        "Fecha de proceso desde el nombre de la cartera"
        + (f" ({sugerida:%d-%m-%Y})" if sugerida else ""),
        value=sugerida is not None)
    fecha = None if usar_nombre else st.sidebar.date_input(
        "Fecha de proceso", value=sugerida or date.today(), format="DD-MM-YYYY")
    return grid, cartera, fecha


# --------------------------------------------------------------------------
# Presentación
# --------------------------------------------------------------------------
def _num_cl(x, dec=0):
    """Número con formato chileno: punto de miles y coma decimal."""
    if x is None or (isinstance(x, float) and x != x):
        return "-"
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _clp(x):
    return f"$ {_num_cl(x)}" if x is not None else "-"


def _fecha_cl(iso):
    """'2026-08-21' -> '21-08-2026'."""
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d-%m-%Y")
    except ValueError:
        return iso


_COLS_FECHA = ("Fecha Inicio", "Fecha Vencimiento")


def _tabla(filas, columnas):
    df = pd.DataFrame(filas)
    if df.empty:
        return df
    df = df[[c for c in columnas if c in df.columns]].rename(columns=columnas)
    for c in _COLS_FECHA:
        if c in df.columns:
            df[c] = df[c].map(_fecha_cl)
    return df


def _descarga_csv(df, nombre):
    buf = io.StringIO()
    df.to_csv(buf, sep=";", decimal=",", index=False)
    st.download_button("Descargar CSV", buf.getvalue().encode("utf-8-sig"),
                       file_name=nombre, mime="text/csv")


def _formatos(df):
    fmt = {}
    for c in df.columns:
        if "(CLP)" in c or c == "Monto Principal":
            fmt[c] = lambda x: _num_cl(x, 0)
        elif c in ("Strike", "Fixing", "Prima (USD)", "P&L No Realizado (USD)"):
            fmt[c] = lambda x: _num_cl(x, 2)
        elif c in ("Forward", "Volatilidad (%)"):
            fmt[c] = lambda x: _num_cl(x, 4)
    return fmt


def _avisos(res):
    for a in res.get("avisos") or []:
        st.warning(a)


def _resultado(clave, ruta, insumos, etiqueta):
    """Llama a la API al presionar el botón y conserva el resultado en la
    sesión (las descargas recargan la página). Si cambian los insumos, el
    resultado anterior se descarta."""
    if insumos is None:
        st.session_state.pop(clave, None)
        return None
    firma = (insumos[0][0], len(insumos[0][1]), insumos[1][0],
             len(insumos[1][1]), insumos[2])
    guardado = st.session_state.get(clave)
    if guardado and guardado[0] != firma:
        st.session_state.pop(clave, None)
    if st.button(etiqueta, type="primary"):
        with st.spinner("Calculando..."):
            res = _post(ruta, insumos)
        if res is None:
            st.session_state.pop(clave, None)
            return None
        st.session_state[clave] = (firma, res)
    guardado = st.session_state.get(clave)
    return guardado[1] if guardado else None


def vista_mtm(insumos):
    st.header("Valorización MtM de la cartera vigente")
    st.caption("Garman-Kohlhagen sobre forward USD/CLP, volatilidad interpolada "
               "por plazo y strike, descuento con curva CLP.")
    res = _resultado("res_mtm", "/fx-options/mtm", insumos, "Calcular MtM")
    if res is None:
        return
    t = res["totales"]
    c = st.columns(4)
    c[0].metric("Fecha de proceso", _fecha_cl(res["fecha_proceso"]))
    c[1].metric("Spot USD/CLP", _num_cl(res["spot"], 2))
    c[2].metric("Operaciones vigentes", t["operaciones"])
    c[3].metric("MtM total (CLP)", _clp(t["mtm_clp"]))
    c = st.columns(4)
    c[0].metric("Prima total (CLP)", _clp(t["prima_clp"]))
    c[1].metric("P&L no realizado (CLP)", _clp(t["pl_no_realizado_clp"]))
    c[2].metric("Altas del día", len(res["altas"]))
    _avisos(res)
    df = _tabla(res["operaciones"], COLS_MTM)
    st.dataframe(df.style.format(_formatos(df), na_rep="-"),
                 hide_index=True, width="stretch")
    _descarga_csv(df, f"MtM_{res['fecha_proceso'].replace('-', '')}.csv")


def vista_payoff(insumos):
    st.header("Payoff de operaciones que vencen")
    st.caption("CALL: máx(fixing − strike, 0) · PUT: máx(strike − fixing, 0), "
               "por el monto principal; negativo cuando Nevasa vende.")
    res = _resultado("res_payoff", "/fx-options/payoff", insumos, "Calcular payoff")
    if res is None:
        return
    t = res["totales"]
    c = st.columns(4)
    c[0].metric("Fecha de proceso", _fecha_cl(res["fecha_proceso"]))
    c[1].metric("Vencimientos", t["operaciones"])
    c[2].metric("ITM", t["itm"])
    c[3].metric("Payoff neto (CLP)", _clp(t["payoff_neto_clp"]))
    c = st.columns(4)
    c[0].metric("Recibido (CLP)", _clp(t["recibido_clp"]))
    c[1].metric("Pagado (CLP)", _clp(t["pagado_clp"]))
    c[2].metric("P&L Nevasa (CLP)", _clp(t["pl_clp"]))
    _avisos(res)
    if not res["vencimientos"]:
        st.info("No hay operaciones que venzan en la fecha de proceso.")
        return
    df = _tabla(res["vencimientos"], COLS_PAYOFF)
    df["ITM"] = df["ITM"].map({True: "Sí", False: "No"})
    st.dataframe(df.style.format(_formatos(df), na_rep="-"),
                 hide_index=True, width="stretch")
    _descarga_csv(df, f"Payoff_{res['fecha_proceso'].replace('-', '')}.csv")


def vista_proceso(insumos):
    st.header("Proceso diario")
    st.caption("Genera MtM_AAAAMMDD.xlsx, MtM_AAAAMMDD_vencimientos.xlsx y "
               "resumen_AAAAMMDD.txt, y guarda la foto de la cartera para el "
               "seguimiento de cambios del día siguiente.")
    res = _resultado("res_proceso", "/fx-options/proceso-diario", insumos,
                     "Ejecutar proceso diario")
    if res is None:
        return
    st.success("Proceso terminado.")
    st.code(res["resumen"], language=None)
    for a in res["archivos"]:
        _boton_descarga(a)


def _boton_descarga(nombre):
    try:
        r = requests.get(f"{API_URL}/fx-options/salidas/{nombre}",
                         headers=HEADERS, timeout=60)
    except requests.RequestException as e:
        st.error(f"No se pudo descargar {nombre}: {e}")
        return
    if r.ok:
        st.download_button(f"Descargar {nombre}", r.content, file_name=nombre,
                           key=f"dl_{nombre}")


def vista_salidas():
    st.header("Informes generados")
    try:
        r = requests.get(f"{API_URL}/fx-options/salidas", headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        st.error(f"No se pudo conectar con la API en {API_URL}: {e}")
        return
    if not r.ok:
        st.error(f"Error {r.status_code}: {r.text}")
        return
    archivos = r.json()
    if not archivos:
        st.info("Aún no hay informes generados.")
        return
    elegido = st.selectbox("Informe", archivos)
    if elegido.endswith(".txt"):
        r = requests.get(f"{API_URL}/fx-options/salidas/{elegido}",
                         headers=HEADERS, timeout=30)
        if r.ok:
            st.code(r.content.decode("utf-8"), language=None)
    _boton_descarga(elegido)


# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="MtM Opciones FX", layout="wide")
    if os.path.exists(LOGO):
        st.sidebar.image(LOGO, width=160)
    st.sidebar.title("MtM Opciones FX")
    opcion = OPCIONES[st.sidebar.radio("Menú", list(OPCIONES))]
    if not _api_ok():
        st.error(f"La API no responde en {API_URL}. Iníciala con:  "
                 f"uvicorn api:app --port 8001")
        return
    if opcion == "salidas":
        vista_salidas()
        return
    insumos = _insumos()
    {"mtm": vista_mtm, "payoff": vista_payoff, "proceso": vista_proceso}[opcion](insumos)


main()
