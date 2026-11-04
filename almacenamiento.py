"""
Almacenamiento de los informes (salidas) y de las fotos de cartera (estado).

Dos implementaciones con la misma interfaz:
  - AlmacenLocal:    carpetas salidas/ y estado/ en disco (uso en el PC).
  - AlmacenPostgres: tabla en PostgreSQL (Heroku Postgres), porque el disco
                     de Heroku se borra en cada reinicio del dyno.

almacen_desde_entorno() elige según las variables de entorno:
  DATABASE_URL  definida  -> AlmacenPostgres (Heroku la define al agregar
                             el add-on Heroku Postgres)
  si no                   -> AlmacenLocal(FXO_SALIDAS, FXO_ESTADO)

Tipos de archivo: "salida" (MtM_*.xlsx, *_vencimientos.xlsx, resumen_*.txt)
y "estado" (estado_cartera_AAAAMMDD.json).
"""
from __future__ import annotations

import os

SALIDA = "salida"
ESTADO = "estado"
TIPOS = (SALIDA, ESTADO)


def _validar(tipo, nombre):
    if tipo not in TIPOS:
        raise ValueError(f"tipo de archivo desconocido: {tipo!r}")
    if not nombre or os.path.basename(nombre) != nombre or nombre in (".", ".."):
        raise ValueError(f"nombre de archivo inválido: {nombre!r}")


class AlmacenLocal:
    def __init__(self, dir_salidas, dir_estado):
        self.dirs = {SALIDA: dir_salidas, ESTADO: dir_estado}

    def descripcion(self):
        return f"local ({self.dirs[SALIDA]}, {self.dirs[ESTADO]})"

    def guardar(self, tipo, nombre, contenido: bytes):
        _validar(tipo, nombre)
        os.makedirs(self.dirs[tipo], exist_ok=True)
        with open(os.path.join(self.dirs[tipo], nombre), "wb") as f:
            f.write(contenido)

    def leer(self, tipo, nombre):
        """Contenido del archivo, o None si no existe."""
        _validar(tipo, nombre)
        ruta = os.path.join(self.dirs[tipo], nombre)
        if not os.path.isfile(ruta):
            return None
        with open(ruta, "rb") as f:
            return f.read()

    def listar(self, tipo):
        _validar(tipo, "x")
        d = self.dirs[tipo]
        if not os.path.isdir(d):
            return []
        return sorted(n for n in os.listdir(d)
                      if os.path.isfile(os.path.join(d, n))
                      and not n.startswith(("~$", ".")))


class AlmacenPostgres:
    """Guarda cada archivo como una fila (tipo, nombre, contenido)."""

    _DDL = """
        CREATE TABLE IF NOT EXISTS fxo_archivos (
            tipo        TEXT        NOT NULL,
            nombre      TEXT        NOT NULL,
            contenido   BYTEA       NOT NULL,
            actualizado TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tipo, nombre)
        )"""

    def __init__(self, url):
        import psycopg            # solo se necesita si se usa Postgres
        self._psycopg = psycopg
        self.url = url
        with self._conectar() as cx:
            cx.execute(self._DDL)

    def _conectar(self):
        return self._psycopg.connect(self.url)

    def descripcion(self):
        return "PostgreSQL (tabla fxo_archivos)"

    def guardar(self, tipo, nombre, contenido: bytes):
        _validar(tipo, nombre)
        with self._conectar() as cx:
            cx.execute(
                """INSERT INTO fxo_archivos (tipo, nombre, contenido)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (tipo, nombre)
                   DO UPDATE SET contenido = EXCLUDED.contenido, actualizado = now()""",
                (tipo, nombre, contenido))

    def leer(self, tipo, nombre):
        _validar(tipo, nombre)
        with self._conectar() as cx:
            fila = cx.execute(
                "SELECT contenido FROM fxo_archivos WHERE tipo = %s AND nombre = %s",
                (tipo, nombre)).fetchone()
        return bytes(fila[0]) if fila else None

    def listar(self, tipo):
        _validar(tipo, "x")
        with self._conectar() as cx:
            filas = cx.execute(
                "SELECT nombre FROM fxo_archivos WHERE tipo = %s ORDER BY nombre",
                (tipo,)).fetchall()
        return [f[0] for f in filas]


def almacen_desde_entorno(base_dir):
    url = os.environ.get("DATABASE_URL")
    if url:
        return AlmacenPostgres(url)
    return AlmacenLocal(
        os.environ.get("FXO_SALIDAS", os.path.join(base_dir, "salidas")),
        os.environ.get("FXO_ESTADO", os.path.join(base_dir, "estado")))
