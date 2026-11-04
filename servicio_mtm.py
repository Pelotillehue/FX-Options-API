"""
Servicio de calculo de MtM de Opciones FX (USD/CLP), sin efectos laterales.

Expone el mismo calculo del proceso diario (daily_process.procesar) como
funciones que devuelven datos estructurados (dicts/listas serializables a
JSON), sin escribir planillas ni actualizar la foto de cartera en estado/.
Lo usan la API (api.py) y el menu (menu_mtm.py).

  - valorizar_cartera(...)  -> MtM y P&L no realizado de la cartera vigente
  - calcular_payoff(...)    -> payoff y P&L de las operaciones que vencen
  - calcular_todo(...)      -> ambos, leyendo los archivos una sola vez
"""
from __future__ import annotations

import os
import re
from datetime import date

from load_market import load_market
from daily_process import (
    leer_operaciones, clasificar, _op_obj, _enriquecer_pl, _fx_por_calce,
    liquidar_vencimientos, pl_vencimiento,
)

_PAT_FECHA_OPS = re.compile(r"al (\d{4})_(\d{2})_(\d{2})")


def fecha_desde_nombre(nombre):
    """Fecha de proceso desde el nombre del archivo de cartera
    ('... al YYYY_MM_DD.xlsx'); None si no la trae."""
    m = _PAT_FECHA_OPS.search(os.path.basename(nombre or ""))
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _num(x):
    return float(x) if isinstance(x, (int, float)) else None


def _datos_op(o):
    """Campos descriptivos de una operacion, comunes a MtM y vencimientos."""
    return dict(
        folio=o["folio"], fecha_inicio=o["inicio"], fecha_vencimiento=o["venc"],
        plazo_dias=(o["venc"] - o["inicio"]).days, par=o.get("par"),
        contraparte=o.get("contraparte"), lado=o.get("lado"), tipo=o.get("tipo"),
        modalidad=o.get("modalidad"), entrega=o.get("entrega"),
        moneda=o.get("moneda"), nominal=_num(o.get("nominal")),
        strike=_num(o.get("strike")),
    )


def _preparar(grid_path, ops_path, fecha_proceso=None, nombre_ops=None):
    """Lee cartera y mercado y clasifica la cartera para la fecha de proceso.
    La fecha se toma, en orden: argumento, nombre del archivo de cartera,
    fecha del grid."""
    if fecha_proceso is None:
        fecha_proceso = fecha_desde_nombre(nombre_ops or ops_path)
    ops, avisos = leer_operaciones(ops_path)
    md = load_market(grid_path, fecha_proceso=fecha_proceso)
    avisos = list(avisos) + [f"datos de mercado: {a}" for a in (md.avisos or [])]
    vigentes, vencen, altas = clasificar(ops, md.fecha_proceso)
    return md, vigentes, vencen, altas, avisos


def _bloque_mtm(md, vigentes):
    resultados = [_op_obj(o).valorizar(md) for o in vigentes]
    _enriquecer_pl(vigentes, resultados)
    filas = []
    for o, r in zip(vigentes, resultados):
        fila = _datos_op(o)
        fila.update(
            dias_al_vencimiento=r["dias"], spot=r["spot"], forward=r["forward"],
            volatilidad=r["vol"],
            prima_clp_por_usd=_num(o.get("prima_clp_usd")),
            prima_pct_monto_usd=_num(o.get("prima_pct")),
            prima_clp=_num(r.get("prima_clp_disp")),
            prima_usd=_num(r.get("prima_usd_disp")),
            mtm_clp=r["mtm"], pl_no_realizado_clp=r["pl_clp"],
            pl_no_realizado_usd=r.get("pl_usd"), fx_prima=r.get("fx_prima"),
        )
        filas.append(fila)
    totales = dict(
        operaciones=len(filas),
        mtm_clp=sum(f["mtm_clp"] for f in filas),
        prima_clp=sum(f["prima_clp"] or 0 for f in filas),
        pl_no_realizado_clp=sum(f["pl_no_realizado_clp"] or 0 for f in filas),
    )
    return filas, totales


def _bloque_payoff(md, vencen):
    liqs, sin_fixing = liquidar_vencimientos(vencen, md.spot)
    fx_calce = _fx_por_calce(vencen)
    filas = []
    for o, liq in zip(vencen, liqs):
        pv = pl_vencimiento(o, liq, vencen, fx_calce)
        fila = _datos_op(o)
        fila.update(
            fixing=liq["fixing"], fuente_fixing=liq["fuente_fixing"],
            itm=liq["itm"],
            prima_clp_por_usd=_num(o.get("prima_clp_usd")),
            prima_pct_monto_usd=_num(o.get("prima_pct")),
            prima_clp=_num(o.get("prima_clp")), prima_usd=_num(o.get("prima_usd")),
            payoff_clp=liq["flujo_pago_clp"], pl_clp=pv["pl_clp"],
            fx_prima=pv["fx_prima"],
        )
        filas.append(fila)
    pagos = [f["payoff_clp"] for f in filas]
    totales = dict(
        operaciones=len(filas), itm=sum(1 for f in filas if f["itm"]),
        payoff_neto_clp=sum(pagos),
        recibido_clp=sum(p for p in pagos if p > 0),
        pagado_clp=-sum(p for p in pagos if p < 0),
        pl_clp=sum(f["pl_clp"] for f in filas),
    )
    avisos = []
    if sin_fixing:
        avisos.append(
            f"vencimientos sin fixing USDOBS informado en la cartera: "
            f"{', '.join(sin_fixing)}. Se usó el USDCLP observado (spot) de "
            f"la fecha de proceso ({md.spot:,.2f}) en su lugar.")
    return filas, totales, avisos


def _cabecera(md, altas, avisos):
    return dict(fecha_proceso=md.fecha_proceso, spot=md.spot,
                altas=[o["folio"] for o in altas], avisos=avisos)


def valorizar_cartera(grid_path, ops_path, fecha_proceso=None, nombre_ops=None):
    """MtM (Garman-Kohlhagen) y P&L no realizado de la cartera vigente
    (inicio <= fecha_proceso < vencimiento)."""
    md, vigentes, _vencen, altas, avisos = _preparar(
        grid_path, ops_path, fecha_proceso, nombre_ops)
    filas, totales = _bloque_mtm(md, vigentes)
    out = _cabecera(md, altas, avisos)
    out.update(operaciones=filas, totales=totales)
    return out


def calcular_payoff(grid_path, ops_path, fecha_proceso=None, nombre_ops=None):
    """Payoff y P&L realizado de las operaciones que vencen en la fecha de
    proceso, con fixing USDOBS (o spot si la cartera no lo informa)."""
    md, _vig, vencen, altas, avisos = _preparar(
        grid_path, ops_path, fecha_proceso, nombre_ops)
    filas, totales, av = _bloque_payoff(md, vencen)
    out = _cabecera(md, altas, avisos + av)
    out.update(vencimientos=filas, totales=totales)
    return out


def calcular_todo(grid_path, ops_path, fecha_proceso=None, nombre_ops=None):
    """MtM de la cartera vigente y payoff de los vencimientos en una pasada."""
    md, vigentes, vencen, altas, avisos = _preparar(
        grid_path, ops_path, fecha_proceso, nombre_ops)
    f_mtm, t_mtm = _bloque_mtm(md, vigentes)
    f_pay, t_pay, av = _bloque_payoff(md, vencen)
    out = _cabecera(md, altas, avisos + av)
    out.update(mtm=dict(operaciones=f_mtm, totales=t_mtm),
               payoff=dict(vencimientos=f_pay, totales=t_pay))
    return out
