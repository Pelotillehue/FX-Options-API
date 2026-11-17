"""
API REST de valorización de Opciones FX (USD/CLP).

Endpoints (prefijo /fx-options):
  GET  /salud                    estado del servicio

  Con datos en JSON (operaciones y mercado del Valorizador de Derivados):
  POST /valorizacion             MtM y griegas (Delta, Gamma, Vega) de una lista
                                 de operaciones a una fecha
  POST /liquidacion              payoff de operaciones con el fixing del vencimiento

  Con las planillas del correo (Grid Vol y Operaciones Diarias):
  POST /mtm                      MtM y P&L no realizado de la cartera vigente
  POST /payoff                   payoff y P&L de las operaciones que vencen
  POST /calculo                  MtM + payoff en una sola llamada
  POST /proceso-diario           proceso completo: genera MtM_*.xlsx,
                                 MtM_*_vencimientos.xlsx y resumen_*.txt y
                                 guarda la foto de la cartera (estado)
  GET  /salidas                  lista los informes generados
  GET  /salidas/{archivo}        descarga un informe

Los POST reciben multipart/form-data con:
  grid           Grid Vol Bloomberg Creasys.xlsx
  operaciones    Operaciones Diarias ... al YYYY_MM_DD.xlsx
  fecha_proceso  (opcional) YYYY-MM-DD; si no viene, se toma del nombre del
                 archivo de operaciones y, en último caso, del grid.

/mtm, /payoff y /calculo no guardan nada. Los informes y las fotos de cartera
de /proceso-diario se guardan en disco (salidas/, estado/) o, si existe la
variable DATABASE_URL (Heroku Postgres), en la base de datos.

Seguridad: si se define la variable FXO_API_KEY, todos los endpoints salvo
/salud exigen el encabezado  X-API-Key: <clave>.

Uso local:
  uvicorn api:app --reload --port 8001
  Documentación interactiva: http://127.0.0.1:8001/docs
"""
from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from contextlib import contextmanager
from datetime import date
from typing import Literal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response

import servicio_mtm
from almacenamiento import ESTADO, SALIDA, almacen_desde_entorno
from daily_process import procesar
from dateparse import parse_fecha

BASE = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.1.0"

app = FastAPI(
    title="API Valorización Opciones FX USD/CLP",
    description="MtM (Garman-Kohlhagen) de la cartera vigente y payoff de "
                "vencimientos con fixing USDOBS.",
    version=VERSION,
)

_almacen = None


def almacen():
    """Almacenamiento de informes y estado (se crea en el primer uso)."""
    global _almacen
    if _almacen is None:
        _almacen = almacen_desde_entorno(BASE)
    return _almacen


def verificar_clave(x_api_key: Optional[str] = Header(None)):
    clave = os.environ.get("FXO_API_KEY")
    if clave and not (x_api_key and secrets.compare_digest(x_api_key, clave)):
        raise HTTPException(401, "API key inválida o ausente (encabezado X-API-Key)")


protegido = [Depends(verificar_clave)]


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _fecha(valor: Optional[str]) -> Optional[date]:
    if valor is None or not str(valor).strip():
        return None
    f = parse_fecha(valor)
    if f is None:
        raise HTTPException(422, f"fecha_proceso no reconocida: {valor!r} "
                                 f"(usar YYYY-MM-DD)")
    return f


def _nombre_seguro(nombre: Optional[str], defecto: str) -> str:
    """Nombre de archivo sin rutas (se conserva porque trae la fecha)."""
    base = os.path.basename((nombre or "").replace("\\", "/")).strip()
    return base or defecto


@contextmanager
def _archivos_temporales(grid: UploadFile, operaciones: UploadFile):
    """Guarda los adjuntos en una carpeta temporal con su nombre original y
    entrega (carpeta, ruta_grid, ruta_operaciones). Se borra al salir."""
    for up, campo in ((grid, "grid"), (operaciones, "operaciones")):
        if not (up.filename or "").lower().endswith((".xlsx", ".xlsm")):
            raise HTTPException(422, f"'{campo}' debe ser un archivo .xlsx")
    tmp = tempfile.mkdtemp(prefix="fxo_")
    try:
        entrada = os.path.join(tmp, "entrada")
        os.makedirs(entrada)
        rutas = []
        for up, defecto in ((grid, "grid.xlsx"), (operaciones, "operaciones.xlsx")):
            ruta = os.path.join(entrada, _nombre_seguro(up.filename, defecto))
            with open(ruta, "wb") as f:
                shutil.copyfileobj(up.file, f)
            rutas.append(ruta)
        if rutas[0] == rutas[1]:
            raise HTTPException(422, "grid y operaciones tienen el mismo nombre")
        yield tmp, rutas[0], rutas[1]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ejecutar(funcion, *args, **kwargs):
    """Traduce errores de lectura de los insumos a HTTP 422."""
    try:
        return funcion(*args, **kwargs)
    except HTTPException:
        raise
    except KeyError as e:
        raise HTTPException(422, f"hoja o columna no encontrada en los insumos: {e}")
    except Exception as e:  # archivo dañado, formato inesperado, etc.
        raise HTTPException(422, f"no se pudo procesar los insumos: "
                                 f"{type(e).__name__}: {e}")


_TIPOS_MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain; charset=utf-8",
}


# --------------------------------------------------------------------------
# Esquemas JSON. Aceptan los objetos tal como los entrega la API del
# Valorizador de Derivados (los campos extra se ignoran).
# --------------------------------------------------------------------------
class _Flexible(BaseModel):
    model_config = ConfigDict(extra="ignore")


class NodoCurva(_Flexible):
    """Nodo de GET /mercado/cotizaciones-swap/IRS_CLPCAM, tasas en tanto por uno."""
    dias: int = Field(gt=0)
    tasa_clp: Optional[float] = None
    tasa_usd: Optional[float] = None


class PuntoVolatilidad(_Flexible):
    """Plazo de GET /mercado/volatilidades/USDCLP, en tanto por uno. Basta
    call/put de 25 y 10 delta o RR/BF."""
    plazo: str
    atm: float = Field(gt=0)
    call25: Optional[float] = None
    put25: Optional[float] = None
    call10: Optional[float] = None
    put10: Optional[float] = None
    rr25: Optional[float] = None
    bf25: Optional[float] = None
    rr10: Optional[float] = None
    bf10: Optional[float] = None


class Mercado(_Flexible):
    spot: float = Field(gt=0, description="USDCLP spot")
    curva: list[NodoCurva] = Field(min_length=1)
    volatilidades: list[PuntoVolatilidad] = Field(min_length=1)


class OperacionFxOption(_Flexible):
    """Campos de GET /fx-options del Valorizador que usa el cálculo."""
    deal_number: str
    posicion: str = Field(description="COMPRA o VENTA")
    tipo_opcion: str = Field(description="CALL o PUT")
    nocional: float = Field(gt=0)
    strike: float = Field(gt=0)
    fecha_vencimiento: date
    fecha_negociacion: Optional[date] = None
    moneda_principal: str = "USD"
    moneda_secundaria: str = "CLP"
    tipo_ejercicio: str = "EUROPEA"


class SolicitudValorizacion(BaseModel):
    fecha_proceso: date
    moneda: Literal["CLP", "USD"] = "CLP"
    mercado: Mercado
    operaciones: list[OperacionFxOption]


class SolicitudLiquidacion(BaseModel):
    fixing: float = Field(gt=0, description="USD observado de la fecha de vencimiento")
    operaciones: list[OperacionFxOption]


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@app.get("/fx-options/salud", tags=["Servicio"])
def salud():
    return {"estado": "ok", "version": VERSION}


@app.post("/fx-options/valorizacion", tags=["Cálculo con JSON"], dependencies=protegido,
          summary="MtM y griegas de operaciones con mercado en JSON")
def valorizacion(sol: SolicitudValorizacion):
    """MtM (Garman-Kohlhagen) de cada operación a `fecha_proceso`, con el
    spot y la curva de la tabla IRS CLPCAM y la superficie de volatilidad
    USDCLP. Devuelve además Delta spot, Gamma spot y Vega de cada operación,
    unitarias (`delta`, `gamma`, `vega`) y en monto con signo desde la
    institución (`delta_usd`, `gamma_usd_1pct`, `vega_clp_1pt`), y sus totales.
    Una operación que no se puede valorizar queda con `mtm` nulo y el
    motivo en `error`. La selección de operaciones vigentes la hace quien
    llama."""
    try:
        return servicio_mtm.valorizar_operaciones(
            sol.fecha_proceso, sol.mercado.model_dump(),
            [o.model_dump() for o in sol.operaciones], sol.moneda)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/fx-options/liquidacion", tags=["Cálculo con JSON"], dependencies=protegido,
          summary="Payoff de operaciones al vencimiento")
def liquidacion(sol: SolicitudLiquidacion):
    """Payoff en CLP con el fixing: CALL máx(fixing − strike, 0), PUT
    máx(strike − fixing, 0), por el nocional; negativo si la institución vende."""
    try:
        return servicio_mtm.liquidar_operaciones(
            sol.fixing, [o.model_dump() for o in sol.operaciones])
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/fx-options/mtm", tags=["Cálculo"], dependencies=protegido,
          summary="MtM de la cartera vigente")
def mtm(grid: UploadFile = File(..., description="Grid Vol Bloomberg Creasys.xlsx"),
        operaciones: UploadFile = File(..., description="Operaciones Diarias ... .xlsx"),
        fecha_proceso: Optional[str] = Form(None, description="YYYY-MM-DD")):
    """Valoriza cada operación vigente (inicio ≤ fecha < vencimiento) con
    Garman-Kohlhagen sobre forward, superficie de volatilidad interpolada por
    plazo y strike, y descuento con la curva CLP. Devuelve MtM y P&L no
    realizado por operación y totales."""
    fp = _fecha(fecha_proceso)
    with _archivos_temporales(grid, operaciones) as (_t, g, o):
        return _ejecutar(servicio_mtm.valorizar_cartera, g, o, fp)


@app.post("/fx-options/payoff", tags=["Cálculo"], dependencies=protegido,
          summary="Payoff de las operaciones que vencen")
def payoff(grid: UploadFile = File(...), operaciones: UploadFile = File(...),
           fecha_proceso: Optional[str] = Form(None, description="YYYY-MM-DD")):
    """Liquida las operaciones con vencimiento en la fecha de proceso:
    payoff = max(fixing − strike, 0) para CALL, max(strike − fixing, 0) para
    PUT, por el nominal, con signo negativo si Nevasa vende. Usa el fixing
    USDOBS de la cartera; si falta, el spot del grid (queda en avisos)."""
    fp = _fecha(fecha_proceso)
    with _archivos_temporales(grid, operaciones) as (_t, g, o):
        return _ejecutar(servicio_mtm.calcular_payoff, g, o, fp)


@app.post("/fx-options/calculo", tags=["Cálculo"], dependencies=protegido,
          summary="MtM y payoff en una llamada")
def calculo(grid: UploadFile = File(...), operaciones: UploadFile = File(...),
            fecha_proceso: Optional[str] = Form(None, description="YYYY-MM-DD")):
    fp = _fecha(fecha_proceso)
    with _archivos_temporales(grid, operaciones) as (_t, g, o):
        return _ejecutar(servicio_mtm.calcular_todo, g, o, fp)


@app.post("/fx-options/proceso-diario", tags=["Proceso diario"],
          dependencies=protegido, summary="Genera los informes del día")
def proceso_diario(grid: UploadFile = File(...), operaciones: UploadFile = File(...),
                   fecha_proceso: Optional[str] = Form(None, description="YYYY-MM-DD")):
    """Mismo proceso que `python daily_process.py`: genera las planillas de
    MtM y vencimientos y el resumen, y guarda la foto de la cartera para el
    seguimiento de cambios del día siguiente (la comparación usa la foto de
    la última fecha anterior ya procesada)."""
    fp = _fecha(fecha_proceso)
    alm = almacen()
    with _archivos_temporales(grid, operaciones) as (tmp, g, o):
        dir_sal = os.path.join(tmp, "salidas")
        dir_est = os.path.join(tmp, "estado")
        os.makedirs(dir_est)
        # el proceso trabaja sobre carpetas: se le entregan las fotos guardadas
        for nombre in alm.listar(ESTADO):
            contenido = alm.leer(ESTADO, nombre)
            if contenido is not None:
                with open(os.path.join(dir_est, nombre), "wb") as f:
                    f.write(contenido)
        previos = set(os.listdir(dir_est))
        res = _ejecutar(procesar, g, o, fp, dir_sal, estado_dir=dir_est)
        # y se guardan los informes y la foto nueva (o reescrita) del día
        for ruta in res["archivos"]:
            with open(ruta, "rb") as f:
                alm.guardar(SALIDA, os.path.basename(ruta), f.read())
        foto = f"estado_cartera_{res['fecha']:%Y%m%d}.json"
        for nombre in set(os.listdir(dir_est)) - previos | {foto}:
            ruta = os.path.join(dir_est, nombre)
            if os.path.isfile(ruta):
                with open(ruta, "rb") as f:
                    alm.guardar(ESTADO, nombre, f.read())
    res = dict(res)
    res["archivos"] = [os.path.basename(a) for a in res["archivos"]]
    return res


@app.get("/fx-options/salidas", tags=["Proceso diario"], dependencies=protegido,
         summary="Lista los informes generados")
def listar_salidas():
    return sorted(almacen().listar(SALIDA), reverse=True)


@app.get("/fx-options/salidas/{archivo}", tags=["Proceso diario"],
         dependencies=protegido, summary="Descarga un informe")
def descargar_salida(archivo: str):
    try:
        contenido = almacen().leer(SALIDA, archivo)
    except ValueError:
        contenido = None
    if contenido is None:
        raise HTTPException(404, f"no existe el informe {archivo!r}")
    ext = os.path.splitext(archivo)[1].lower()
    return Response(contenido, media_type=_TIPOS_MIME.get(ext, "application/octet-stream"),
                    headers={"Content-Disposition": f'attachment; filename="{archivo}"'})
