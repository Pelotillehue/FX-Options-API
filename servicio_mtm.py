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


# ==========================================================================
# Cálculo con datos en JSON (operaciones y mercado del Valorizador de
# Derivados), sin planillas.
# ==========================================================================
from fx_options_valuation import MarketData, Operacion, liquidar_vencimiento
from load_market import tasa_plausible

## Días de cada unidad de plazo de la superficie, como en los tenores
## estándar del motor (1W=7, 1M=30, 18M=540, 1Y=365, 2Y=730).
_DIAS_UNIDAD = {"D": 1, "W": 7, "M": 30, "Y": 365}


def dias_plazo(plazo):
    """'1W' -> 7, '18M' -> 540, '2Y' -> 730."""
    p = str(plazo).strip().upper()
    if len(p) < 2 or p[-1] not in _DIAS_UNIDAD or not p[:-1].isdigit():
        raise ValueError(f"plazo de volatilidad no reconocido: {plazo!r}")
    return int(p[:-1]) * _DIAS_UNIDAD[p[-1]]


def _vol_put_call(p):
    """(put10, put25, atm, call25, call10) en tanto por uno. Usa call/put si
    vienen; si no, los deduce de ATM, RR y BF: call = ATM + RR/2 + BF,
    put = ATM - RR/2 + BF."""
    def g(k):
        return p.get(k)
    if all(g(k) is not None for k in ("call25", "put25", "call10", "put10")):
        return g("put10"), g("put25"), g("atm"), g("call25"), g("call10")
    if all(g(k) is not None for k in ("rr25", "bf25", "rr10", "bf10")):
        a = g("atm")
        return (a - g("rr10") / 2 + g("bf10"), a - g("rr25") / 2 + g("bf25"), a,
                a + g("rr25") / 2 + g("bf25"), a + g("rr10") / 2 + g("bf10"))
    raise ValueError(f"plazo {p.get('plazo')}: faltan call/put o RR/BF de 25 y 10 delta")


def mercado_desde_datos(fecha_proceso, spot, curva, volatilidades):
    """Arma el MarketData del motor desde datos en tanto por uno.

    curva: nodos de la tabla IRS CLPCAM, cada uno con 'dias', 'tasa_clp' y
           'tasa_usd' (puede faltar). Misma regla que la planilla: la curva CLP
           usa todos los nodos con tasa CLP y la USD sólo los que traen tasa USD.
    volatilidades: plazos de la superficie USDCLP con 'plazo', 'atm' y call/put
           (o RR/BF) de 25 y 10 delta.
    """
    if not spot or spot <= 0:
        raise ValueError("spot USDCLP inválido")
    nodos = sorted((dict(n) for n in curva), key=lambda n: n["dias"])
    clp_dias, clp, usd_dias, usd = [], [], [], []
    raw_dias, raw_clp, raw_usd = [], [], []
    avisos = []
    for n in nodos:
        d = float(n["dias"])
        rc = None if n.get("tasa_clp") is None else n["tasa_clp"] * 100
        ru = None if n.get("tasa_usd") is None else n["tasa_usd"] * 100
        if rc is not None and not tasa_plausible(rc):
            avisos.append(f"curva CLP: nodo {int(d)}d con tasa implausible ({rc:.4g}%)")
        if ru is not None and not tasa_plausible(ru):
            avisos.append(f"curva USD: nodo {int(d)}d con tasa implausible ({ru:.4g}%)")
        if rc is not None:
            clp_dias.append(d); clp.append(rc)
        if ru is not None:
            usd_dias.append(d); usd.append(ru)
        if rc is not None and ru is not None:
            raw_dias.append(d); raw_clp.append(rc); raw_usd.append(ru)
    if not clp_dias or not usd_dias:
        raise ValueError("la curva necesita al menos un nodo con tasa CLP y uno con tasa USD")
    puntos = sorted(((dias_plazo(p["plazo"]), _vol_put_call(p)) for p in volatilidades),
                    key=lambda x: x[0])
    if not puntos:
        raise ValueError("la superficie de volatilidad está vacía")
    md = MarketData(
        fecha_proceso=fecha_proceso, spot=float(spot),
        raw_dias=raw_dias, raw_clp=raw_clp, raw_usd=raw_usd,
        vol_tenor_dias=[d for d, _ in puntos],
        vol_smile=[tuple(v * 100 for v in s) for _, s in puntos],
        clp_dias=clp_dias, clp_rates=clp, usd_dias=usd_dias, usd_rates=usd,
        avisos=avisos)
    return md.build()


_LADO = {"COMPRA": "Compra", "VENTA": "Venta"}


def _validar_op(op):
    """Motivo por el que la operación no se puede valorizar, o None."""
    par = (op.get("moneda_principal") or "USD", op.get("moneda_secundaria") or "CLP")
    if tuple(m.upper() for m in par) != ("USD", "CLP"):
        return f"par {par[0]}/{par[1]} no soportado: sólo USD/CLP"
    if (op.get("tipo_ejercicio") or "EUROPEA").upper() != "EUROPEA":
        return "sólo se valorizan opciones europeas"
    if str(op.get("tipo_opcion", "")).upper() not in ("CALL", "PUT"):
        return f"tipo_opcion inválido: {op.get('tipo_opcion')!r}"
    if str(op.get("posicion", "")).upper() not in _LADO:
        return f"posicion inválida: {op.get('posicion')!r}"
    return None


def _operacion_motor(op):
    return Operacion(
        folio=op["deal_number"], fecha_inicio=op.get("fecha_negociacion"),
        fecha_vencimiento=op["fecha_vencimiento"],
        tipo=str(op["tipo_opcion"]).upper(), lado=_LADO[str(op["posicion"]).upper()],
        nominal=float(op["nocional"]), strike=float(op["strike"]))


# Griegas en monto, con signo desde la institución; se suman en los totales.
_GRIEGAS_MONTO = ("delta_usd", "gamma_usd_1pct", "vega_clp_1pt")
# Griegas de cada fila: unitarias (opción comprada) y en monto.
_GRIEGAS = ("delta", "gamma", "vega") + _GRIEGAS_MONTO


def valorizar_operaciones(fecha_proceso, mercado, operaciones, moneda="CLP"):
    """MtM y griegas (Delta spot, Gamma spot y Vega) de cada operación a la
    fecha de proceso.

    Griegas por fila: 'delta', 'gamma' y 'vega' unitarias, de la opción
    comprada (ver fx_options_valuation.griegas_option_fx), y en monto con signo
    desde la institución: 'delta_usd' (posición spot equivalente),
    'gamma_usd_1pct' (cambio de delta_usd si el spot sube 1%) y 'vega_clp_1pt'
    (cambio del MtM en CLP si la volatilidad sube 1 punto). Los totales
    'delta_usd_total', 'gamma_usd_1pct_total' y 'vega_clp_1pt_total' suman las
    operaciones valorizadas. Las griegas no dependen de 'moneda'.

    mercado: dict con 'spot', 'curva' y 'volatilidades' (ver mercado_desde_datos).
    operaciones: dicts con los campos de GET /fx-options del Valorizador
      (deal_number, posicion, tipo_opcion, nocional, strike, fecha_vencimiento,
      moneda_principal, moneda_secundaria).
    moneda: 'CLP' o 'USD'; el MtM en USD es el de CLP dividido por el spot.
    Una operación que no se puede valorizar queda con mtm None y el motivo en
    'error'; no detiene las demás."""
    moneda = (moneda or "CLP").upper()
    if moneda not in ("CLP", "USD"):
        raise ValueError("moneda debe ser CLP o USD")
    md = mercado_desde_datos(fecha_proceso, mercado["spot"], mercado["curva"],
                             mercado["volatilidades"])
    filas = []
    for op in operaciones:
        fila = dict(deal_number=op.get("deal_number"), dias=None, forward=None,
                    volatilidad=None, factor_descuento=None, mtm_clp=None, mtm=None,
                    **dict.fromkeys(_GRIEGAS), error=_validar_op(op))
        if fila["error"] is None:
            try:
                dias = (servicio_fecha(op["fecha_vencimiento"]) - md.fecha_proceso).days
                if dias <= 0:
                    raise ValueError("la operación ya venció a la fecha de proceso")
                motor = _operacion_motor(op)
                r = motor.valorizar(md)
                g = motor.griegas(md) or {}
                fila.update({k: g.get(k) for k in _GRIEGAS})
                fila.update(dias=r["dias"], forward=r["forward"],
                            volatilidad=r["vol"] / 100, factor_descuento=md.df_clp_at(r["dias"]),
                            mtm_clp=r["mtm"],
                            mtm=r["mtm"] if moneda == "CLP" else r["mtm"] / md.spot)
            except (ValueError, TypeError, KeyError, ZeroDivisionError) as e:
                fila["error"] = str(e)
        filas.append(fila)
    ok = [f for f in filas if f["mtm"] is not None]
    return dict(fecha_proceso=md.fecha_proceso, moneda=moneda, spot=md.spot,
                mtm_total=sum(f["mtm"] for f in ok),
                **{f"{k}_total": sum(f[k] or 0.0 for f in ok) for k in _GRIEGAS_MONTO},
                valorizadas=len(ok),
                con_error=len(filas) - len(ok), avisos=list(md.avisos or []),
                operaciones=filas)


def liquidar_operaciones(fixing, operaciones):
    """Payoff en CLP de cada operación con el fixing (USD observado) del
    vencimiento: CALL máx(fixing − strike, 0), PUT máx(strike − fixing, 0), por
    el nocional; negativo si la institución vende."""
    if not fixing or fixing <= 0:
        raise ValueError("fixing inválido")
    filas = []
    for op in operaciones:
        fila = dict(deal_number=op.get("deal_number"), fixing=float(fixing),
                    itm=None, payoff_clp=None, error=_validar_op(op))
        if fila["error"] is None:
            liq = liquidar_vencimiento(_operacion_motor(op), float(fixing))
            fila.update(itm=liq["itm"], payoff_clp=liq["flujo_pago_clp"])
        filas.append(fila)
    pagos = [f["payoff_clp"] for f in filas if f["payoff_clp"] is not None]
    return dict(fixing=float(fixing), payoff_neto_clp=sum(pagos),
                recibido_clp=sum(p for p in pagos if p > 0),
                pagado_clp=-sum(p for p in pagos if p < 0),
                itm=sum(1 for f in filas if f["itm"]), operaciones=filas)


def servicio_fecha(x):
    """date desde date/datetime/str ISO."""
    from datetime import datetime
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])
