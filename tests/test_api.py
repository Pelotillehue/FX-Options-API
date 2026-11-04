"""Pruebas de la API (api.py) y del servicio (servicio_mtm.py).

Usan el grid de mercado de prueba (tests/data) y una cartera sintética, por lo
que corren en cualquier máquina. Se saltan si no está instalado FastAPI.
"""
import os
from datetime import date, datetime

import openpyxl
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402
from almacenamiento import AlmacenLocal  # noqa: E402
import servicio_mtm  # noqa: E402
from fx_options_valuation import Operacion  # noqa: E402
from load_market import load_market  # noqa: E402

GRID = os.path.join(os.path.dirname(__file__), "data",
                    "Grid Vol Bloomberg Creasys 2026-07-14.xlsx")
FECHA = date(2026, 7, 14)
NOMBRE_OPS = "Operaciones Diarias Opciones y Cartera Vigente al 2026_07_14.xlsx"

# folio, inicio, venc, lado, tipo, nominal, strike, prima_clp, usdobs
CARTERA = [
    ("VIG-C", date(2026, 7, 1), date(2026, 9, 30), "Compra", "CALL", 1_000_000, 930.0, -5_000_000, None),
    ("VIG-V", date(2026, 7, 1), date(2026, 9, 30), "Venta", "CALL", 1_000_000, 930.0, 6_000_000, None),
    ("ALTA-P", FECHA, date(2026, 12, 15), "Compra", "PUT", 500_000, 900.0, -2_000_000, None),
    ("VEN-ITM", date(2026, 6, 1), FECHA, "Venta", "CALL", 200_000, 900.0, 1_500_000, 950.0),
    ("VEN-OTM", date(2026, 6, 1), FECHA, "Compra", "PUT", 300_000, 900.0, -800_000, 950.0),
    ("VEN-SINFIX", date(2026, 6, 1), FECHA, "Compra", "CALL", 100_000, 800.0, -300_000, None),
]


def _escribir_cartera(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Operaciones Diarias"
    ws.cell(1, 1, "Folio")
    for r, (fol, ini, ven, lado, tipo, nom, k, prima, obs) in enumerate(CARTERA, 2):
        ws.cell(r, 1, fol)
        ws.cell(r, 2, datetime(ini.year, ini.month, ini.day))
        ws.cell(r, 3, datetime(ven.year, ven.month, ven.day))
        ws.cell(r, 5, "USD/CLP")
        ws.cell(r, 6, "CP")
        ws.cell(r, 7, lado)
        ws.cell(r, 8, tipo)
        ws.cell(r, 9, "EUROPEA")
        ws.cell(r, 10, "Compensación")
        ws.cell(r, 11, "USD")
        ws.cell(r, 12, nom)
        ws.cell(r, 13, k)
        ws.cell(r, 15, prima / nom)
        ws.cell(r, 16, prima)
        if obs is not None:
            ws.cell(r, 25, obs)
    wb.save(path)


@pytest.fixture
def ops_path(tmp_path):
    p = tmp_path / NOMBRE_OPS
    _escribir_cartera(p)
    return str(p)


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.delenv("FXO_API_KEY", raising=False)
    monkeypatch.setattr(api, "_almacen", AlmacenLocal(str(tmp_path / "salidas"),
                                                      str(tmp_path / "estado")))
    return TestClient(api.app)


def _files(ops_path):
    return {
        "grid": (os.path.basename(GRID), open(GRID, "rb")),
        "operaciones": (os.path.basename(ops_path), open(ops_path, "rb")),
    }


# ---------------------------------------------------------------- servicio
def test_servicio_mtm_coincide_con_motor(ops_path):
    res = servicio_mtm.valorizar_cartera(GRID, ops_path)
    assert res["fecha_proceso"] == FECHA                     # desde el nombre
    assert [o["folio"] for o in res["operaciones"]] == ["VIG-C", "VIG-V", "ALTA-P"]
    assert res["altas"] == ["ALTA-P"]
    md = load_market(GRID, FECHA)
    for fila in res["operaciones"]:
        c = next(x for x in CARTERA if x[0] == fila["folio"])
        esperado = Operacion(c[0], c[1], c[2], c[4], c[3], c[5], c[6], c[7]).valorizar(md)
        assert fila["mtm_clp"] == pytest.approx(esperado["mtm"], abs=1e-6)
        assert fila["pl_no_realizado_clp"] == pytest.approx(c[7] + esperado["mtm"], abs=1e-6)
    # el par calzado compra/venta suma MtM cero
    mtm = {f["folio"]: f["mtm_clp"] for f in res["operaciones"]}
    assert mtm["VIG-C"] + mtm["VIG-V"] == pytest.approx(0.0, abs=1e-6)
    assert res["totales"]["mtm_clp"] == pytest.approx(sum(mtm.values()))


def test_servicio_payoff(ops_path):
    res = servicio_mtm.calcular_payoff(GRID, ops_path)
    v = {f["folio"]: f for f in res["vencimientos"]}
    assert set(v) == {"VEN-ITM", "VEN-OTM", "VEN-SINFIX"}
    # Nevasa vende CALL 900 con fixing 950: paga 50 x 200.000
    assert v["VEN-ITM"]["payoff_clp"] == pytest.approx(-10_000_000)
    assert v["VEN-ITM"]["itm"] is True
    assert v["VEN-ITM"]["pl_clp"] == pytest.approx(1_500_000 - 10_000_000)
    assert v["VEN-OTM"]["payoff_clp"] == 0 and v["VEN-OTM"]["itm"] is False
    # sin USDOBS: usa el spot del grid y lo avisa
    md = load_market(GRID, FECHA)
    assert v["VEN-SINFIX"]["fuente_fixing"] == "Spot"
    assert v["VEN-SINFIX"]["fixing"] == pytest.approx(md.spot)
    assert v["VEN-SINFIX"]["payoff_clp"] == pytest.approx((md.spot - 800) * 100_000)
    assert any("VEN-SINFIX" in a for a in res["avisos"])
    t = res["totales"]
    assert t["itm"] == 2
    assert t["pagado_clp"] == pytest.approx(10_000_000)
    assert t["payoff_neto_clp"] == pytest.approx(t["recibido_clp"] - t["pagado_clp"])


# --------------------------------------------------------------------- API
def test_salud(cliente):
    assert cliente.get("/fx-options/salud").json()["estado"] == "ok"


def test_api_mtm(cliente, ops_path):
    r = cliente.post("/fx-options/mtm", files=_files(ops_path))
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["fecha_proceso"] == "2026-07-14"
    assert js["totales"]["operaciones"] == 3
    esperado = servicio_mtm.valorizar_cartera(GRID, ops_path)
    assert js["totales"]["mtm_clp"] == pytest.approx(esperado["totales"]["mtm_clp"])


def test_api_payoff_y_calculo(cliente, ops_path):
    r = cliente.post("/fx-options/payoff", files=_files(ops_path))
    assert r.status_code == 200, r.text
    assert r.json()["totales"]["operaciones"] == 3
    r = cliente.post("/fx-options/calculo", files=_files(ops_path))
    js = r.json()
    assert js["mtm"]["totales"]["operaciones"] == 3
    assert js["payoff"]["totales"]["itm"] == 2


def test_api_fecha_explicita(cliente, ops_path):
    # al 2026-09-30 vencen VIG-C y VIG-V; ninguna queda vigente salvo ALTA-P
    r = cliente.post("/fx-options/payoff", files=_files(ops_path),
                     data={"fecha_proceso": "2026-09-30"})
    assert r.status_code == 200, r.text
    assert {f["folio"] for f in r.json()["vencimientos"]} == {"VIG-C", "VIG-V"}


def test_api_errores(cliente, ops_path):
    r = cliente.post("/fx-options/mtm", files=_files(ops_path),
                     data={"fecha_proceso": "no-es-fecha"})
    assert r.status_code == 422
    files = _files(ops_path)
    files["operaciones"] = ("cartera.txt", b"x")
    assert cliente.post("/fx-options/mtm", files=files).status_code == 422
    files = _files(ops_path)
    files["operaciones"] = (NOMBRE_OPS, b"no es un excel")
    assert cliente.post("/fx-options/mtm", files=files).status_code == 422


def test_api_proceso_diario_y_descarga(cliente, ops_path):
    r = cliente.post("/fx-options/proceso-diario", files=_files(ops_path))
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["archivos"] == ["MtM_20260714.xlsx", "MtM_20260714_vencimientos.xlsx",
                              "resumen_20260714.txt"]
    assert set(cliente.get("/fx-options/salidas").json()) == set(js["archivos"])
    d = cliente.get("/fx-options/salidas/resumen_20260714.txt")
    assert d.status_code == 200 and "Vencieron 3 operaciones" in d.text
    assert cliente.get("/fx-options/salidas/..%2Fapi.py").status_code == 404
    assert cliente.get("/fx-options/salidas/noexiste.xlsx").status_code == 404


def test_api_proceso_guarda_estado_y_compara(cliente, ops_path):
    """La foto del día queda guardada y la corrida siguiente la usa como base."""
    r = cliente.post("/fx-options/proceso-diario", files=_files(ops_path))
    assert r.status_code == 200, r.text
    assert api._almacen.listar("estado") == ["estado_cartera_20260714.json"]
    # al día siguiente VIG-V desaparece de la cartera sin haber vencido
    wb = openpyxl.load_workbook(ops_path)
    ws = wb["Operaciones Diarias"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == "VIG-V":
            for c in row:
                c.value = None
    sig = os.path.join(os.path.dirname(ops_path),
                       NOMBRE_OPS.replace("2026_07_14", "2026_07_15"))
    wb.save(sig)
    r = cliente.post("/fx-options/proceso-diario", files=_files(sig))
    assert r.status_code == 200, r.text
    assert any("VIG-V" in a and "anticipadamente" in a for a in r.json()["avisos"])
    assert len(api._almacen.listar("estado")) == 2


def test_api_key(cliente, ops_path, monkeypatch):
    monkeypatch.setenv("FXO_API_KEY", "secreta")
    assert cliente.get("/fx-options/salud").status_code == 200       # libre
    assert cliente.post("/fx-options/mtm", files=_files(ops_path)).status_code == 401
    r = cliente.post("/fx-options/mtm", files=_files(ops_path),
                     headers={"X-API-Key": "otra"})
    assert r.status_code == 401
    r = cliente.post("/fx-options/mtm", files=_files(ops_path),
                     headers={"X-API-Key": "secreta"})
    assert r.status_code == 200
