"""Pruebas de los endpoints JSON (/valorizacion y /liquidacion), que reciben
operaciones y mercado como los entrega la API del Valorizador de Derivados."""
import os
from datetime import date

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402
import servicio_mtm  # noqa: E402
from fx_options_valuation import Operacion  # noqa: E402
from load_market import load_market  # noqa: E402

GRID = os.path.join(os.path.dirname(__file__), "data",
                    "Grid Vol Bloomberg Creasys 2026-07-14.xlsx")
FECHA = date(2026, 7, 14)
ETIQUETAS = {1: "1D", 7: "1W", 14: "2W", 21: "3W", 30: "1M", 60: "2M", 90: "3M",
             120: "4M", 180: "6M", 270: "9M", 365: "1Y", 540: "18M", 730: "2Y",
             1095: "3Y", 1460: "4Y", 1825: "5Y"}


@pytest.fixture(scope="module")
def md():
    return load_market(GRID, FECHA)


def _mercado(md, rr_bf=False):
    """Mercado en el formato del Valorizador (tanto por uno) desde el grid."""
    usd = dict(zip(md.usd_dias, md.usd_rates))
    curva = [{"ticker": f"N{int(d)}", "dias": int(d), "tasa_clp": r / 100,
              "tasa_usd": usd[d] / 100 if d in usd else None}
             for d, r in zip(md.clp_dias, md.clp_rates)]
    vols = []
    for d, (p10, p25, atm, c25, c10) in zip(md.vol_tenor_dias, md.vol_smile):
        p = {"plazo": ETIQUETAS[d], "atm": atm / 100}
        if rr_bf:
            p.update(rr25=(c25 - p25) / 100, bf25=((c25 + p25) / 2 - atm) / 100,
                     rr10=(c10 - p10) / 100, bf10=((c10 + p10) / 2 - atm) / 100)
        else:
            p.update(call25=c25 / 100, put25=p25 / 100, call10=c10 / 100, put10=p10 / 100)
        vols.append(p)
    return {"spot": md.spot, "curva": curva, "volatilidades": vols}


OPS = [
    {"deal_number": "C1", "posicion": "COMPRA", "tipo_opcion": "CALL", "nocional": 1_000_000,
     "strike": 930.0, "fecha_vencimiento": "2026-09-30", "estado": "VIGENTE"},
    {"deal_number": "V1", "posicion": "VENTA", "tipo_opcion": "CALL", "nocional": 1_000_000,
     "strike": 930.0, "fecha_vencimiento": "2026-09-30"},
    {"deal_number": "P1", "posicion": "COMPRA", "tipo_opcion": "PUT", "nocional": 500_000,
     "strike": 900.0, "fecha_vencimiento": "2027-03-15"},
]


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.delenv("FXO_API_KEY", raising=False)
    return TestClient(api.app)


@pytest.mark.parametrize("rr_bf", [False, True])
def test_servicio_coincide_con_motor(md, rr_bf):
    res = servicio_mtm.valorizar_operaciones(FECHA, _mercado(md, rr_bf), OPS)
    assert res["con_error"] == 0 and res["valorizadas"] == 3
    for f, o in zip(res["operaciones"], OPS):
        esperado = Operacion(o["deal_number"], None, o["fecha_vencimiento"], o["tipo_opcion"],
                             o["posicion"].capitalize(), o["nocional"], o["strike"]).valorizar(md)
        assert f["mtm_clp"] == pytest.approx(esperado["mtm"], abs=1e-6)
        assert f["volatilidad"] == pytest.approx(esperado["vol"] / 100, abs=1e-12)
        assert f["forward"] == pytest.approx(esperado["forward"], abs=1e-9)
    assert res["operaciones"][0]["mtm"] + res["operaciones"][1]["mtm"] == pytest.approx(0, abs=1e-6)


def test_api_valorizacion(cliente, md):
    cuerpo = {"fecha_proceso": "2026-07-14", "mercado": _mercado(md), "operaciones": OPS}
    r = cliente.post("/fx-options/valorizacion", json=cuerpo)
    assert r.status_code == 200, r.text
    clp = r.json()
    assert clp["valorizadas"] == 3 and clp["moneda"] == "CLP"
    r = cliente.post("/fx-options/valorizacion", json={**cuerpo, "moneda": "USD"})
    usd = r.json()
    assert usd["mtm_total"] == pytest.approx(clp["mtm_total"] / md.spot)


def test_api_valorizacion_errores_por_operacion(cliente, md):
    ops = OPS + [
        {**OPS[0], "deal_number": "EUR", "moneda_principal": "EUR"},
        {**OPS[0], "deal_number": "VENCIDA", "fecha_vencimiento": "2026-07-14"},
        {**OPS[0], "deal_number": "AMERICANA", "tipo_ejercicio": "AMERICANA"},
    ]
    r = cliente.post("/fx-options/valorizacion",
                     json={"fecha_proceso": "2026-07-14", "mercado": _mercado(md), "operaciones": ops})
    js = r.json()
    assert js["valorizadas"] == 3 and js["con_error"] == 3
    err = {f["deal_number"]: f["error"] for f in js["operaciones"] if f["error"]}
    assert "no soportado" in err["EUR"]
    assert "venció" in err["VENCIDA"]
    assert "europeas" in err["AMERICANA"]


def test_api_valorizacion_mercado_invalido(cliente, md):
    m = _mercado(md)
    m["curva"] = [{**n, "tasa_usd": None} for n in m["curva"]]
    r = cliente.post("/fx-options/valorizacion",
                     json={"fecha_proceso": "2026-07-14", "mercado": m, "operaciones": OPS})
    assert r.status_code == 422 and "tasa USD" in r.json()["detail"]
    m = _mercado(md)
    m["volatilidades"][0] = {"plazo": "1D", "atm": 0.1}
    r = cliente.post("/fx-options/valorizacion",
                     json={"fecha_proceso": "2026-07-14", "mercado": m, "operaciones": OPS})
    assert r.status_code == 422


def test_api_liquidacion(cliente):
    r = cliente.post("/fx-options/liquidacion", json={"fixing": 950.0, "operaciones": OPS})
    assert r.status_code == 200, r.text
    js = r.json()
    pay = {f["deal_number"]: f["payoff_clp"] for f in js["operaciones"]}
    assert pay == {"C1": pytest.approx(20_000_000), "V1": pytest.approx(-20_000_000), "P1": 0}
    assert js["itm"] == 2 and js["payoff_neto_clp"] == pytest.approx(0)
    assert js["recibido_clp"] == pytest.approx(20_000_000)


def test_api_json_con_clave(cliente, md, monkeypatch):
    monkeypatch.setenv("FXO_API_KEY", "k")
    r = cliente.post("/fx-options/liquidacion", json={"fixing": 950.0, "operaciones": OPS})
    assert r.status_code == 401
    r = cliente.post("/fx-options/liquidacion", json={"fixing": 950.0, "operaciones": OPS},
                     headers={"X-API-Key": "k"})
    assert r.status_code == 200
