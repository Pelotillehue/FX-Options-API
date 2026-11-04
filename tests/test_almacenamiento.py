"""Pruebas del almacenamiento local y, si hay base de pruebas, de PostgreSQL.

Para probar PostgreSQL define DATABASE_URL_TEST apuntando a una base exclusiva
para pruebas (se crea y se vacía la tabla fxo_archivos)."""
import os

import pytest

from almacenamiento import AlmacenLocal, AlmacenPostgres, ESTADO, SALIDA


def _probar(alm):
    assert alm.listar(SALIDA) == []
    alm.guardar(SALIDA, "MtM_20260714.xlsx", b"\x00\x01excel")
    alm.guardar(SALIDA, "resumen_20260714.txt", "Año ñandú".encode("utf-8"))
    alm.guardar(ESTADO, "estado_cartera_20260714.json", b"{}")
    assert alm.listar(SALIDA) == ["MtM_20260714.xlsx", "resumen_20260714.txt"]
    assert alm.listar(ESTADO) == ["estado_cartera_20260714.json"]
    assert alm.leer(SALIDA, "MtM_20260714.xlsx") == b"\x00\x01excel"
    alm.guardar(SALIDA, "MtM_20260714.xlsx", b"nuevo")            # sobrescribe
    assert alm.leer(SALIDA, "MtM_20260714.xlsx") == b"nuevo"
    assert alm.leer(SALIDA, "noexiste.xlsx") is None
    for malo in ("../api.py", "a/b.txt", "", ".."):
        with pytest.raises(ValueError):
            alm.leer(SALIDA, malo)
    with pytest.raises(ValueError):
        alm.guardar("otro", "x.txt", b"")


def test_almacen_local(tmp_path):
    _probar(AlmacenLocal(str(tmp_path / "s"), str(tmp_path / "e")))


@pytest.mark.skipif(not os.environ.get("DATABASE_URL_TEST"),
                    reason="sin base PostgreSQL de pruebas (DATABASE_URL_TEST)")
def test_almacen_postgres():
    pytest.importorskip("psycopg")
    alm = AlmacenPostgres(os.environ["DATABASE_URL_TEST"])
    with alm._conectar() as cx:
        cx.execute("DELETE FROM fxo_archivos")
    _probar(alm)
