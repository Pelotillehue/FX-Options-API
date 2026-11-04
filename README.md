# API de valorización de Opciones FX (USD/CLP)

API REST (FastAPI) que calcula el MtM de opciones FX europeas USD/CLP y el
payoff de las que vencen. La usa la app del Valorizador de Derivados para
incluir las FX Options en el MtM de cartera: la app le envía las opciones y el
mercado que guarda el valorizador, y la API devuelve el MtM. También puede
trabajar con las planillas del correo diario y trae un menú Streamlit propio. El motor de valorización es el mismo del proyecto
`FX Options Valorización` (Garman-Kohlhagen sobre forward, superficie de
volatilidad Bloomberg interpolada por plazo y strike, descuento con curva CLP),
validado al centavo contra el maestro Excel/VBA.

## Estructura

```
├── api.py                    # API REST (punto de entrada: api:app)
├── servicio_mtm.py           # MtM y payoff como datos (sin escribir archivos)
├── almacenamiento.py         # Informes y fotos de cartera: disco o PostgreSQL
├── menu_mtm.py               # Menú de cálculo de MtM (Streamlit), cliente de la API
├── fx_options_valuation.py   # Motor: curvas, superficie de vol, Black-Scholes FX, payoff
├── load_market.py            # Lee 'Grid Vol Bloomberg Creasys.xlsx'
├── dateparse.py              # Parser de fechas (incluye formato español)
├── daily_process.py          # Proceso diario: cartera, P&L, planillas Excel y resumen
├── Procfile, .python-version # Despliegue en Heroku
├── iniciar.bat               # Levanta API y menú en Windows
├── requirements.txt          # API (lo que instala Heroku)
├── requirements-menu.txt     # Menú Streamlit
├── requirements-dev.txt      # Todo + pruebas
├── assets/logo.png           # Logo de las planillas
├── tests/                    # pytest (tests/data: grid de mercado de prueba)
├── data/                     # (ignorado por git) insumos del correo
├── salidas/                  # (ignorado por git) informes generados en local
└── estado/                   # (ignorado por git) fotos de cartera en local
```

## Instalación local

```powershell
cd "D:\FX Options API"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

## Uso local

Doble clic en `iniciar.bat`, o en dos consolas:

```powershell
uvicorn api:app --reload --port 8001                 # API: http://127.0.0.1:8001/docs
streamlit run menu_mtm.py --server.port 8502         # menú: http://localhost:8502
```

Los puertos 8001 y 8502 evitan chocar con el Valorizador de Derivados
(API en 8000 y app en 8501).

## Endpoints con datos en JSON (los usa el Valorizador de Derivados)

| Método | Ruta | Qué hace |
|---|---|---|
| GET  | `/fx-options/salud` | Estado del servicio (sin clave) |
| POST | `/fx-options/valorizacion` | MtM de una lista de opciones a una fecha, con el mercado enviado |
| POST | `/fx-options/liquidacion` | Payoff de opciones con el fixing del vencimiento |

`/valorizacion` recibe:

```json
{
  "fecha_proceso": "2026-08-21",
  "moneda": "CLP",
  "mercado": {
    "spot": 917.74878,
    "curva": [{"dias": 1, "tasa_clp": 0.0475, "tasa_usd": 0.0431}],
    "volatilidades": [{"plazo": "1M", "atm": 0.1012, "call25": 0.1061, "put25": 0.0995,
                       "call10": 0.1135, "put10": 0.1003}]
  },
  "operaciones": [{"deal_number": "OPT100212", "posicion": "VENTA", "tipo_opcion": "CALL",
                   "nocional": 500000, "strike": 930, "fecha_vencimiento": "2026-08-25"}]
}
```

- `spot` y `curva` son los de `GET /mercado/cotizaciones-swap/IRS_CLPCAM` del valorizador
  (`spot` y `nodos`), y `volatilidades`, los `puntos` de `GET /mercado/volatilidades/USDCLP`;
  se envían tal cual (los campos extra se ignoran). Tasas y volatilidades en tanto por uno.
  En la superficie basta call/put de 25 y 10 delta o RR/BF.
- `operaciones` acepta los objetos de `GET /fx-options` del valorizador tal cual.
- Devuelve por operación `mtm`, `mtm_clp`, `forward`, `volatilidad`, `factor_descuento` y
  `dias`. Una operación que no se puede valorizar (par distinto de USD/CLP, no europea, ya
  vencida) queda con `mtm` nulo y el motivo en `error`, sin detener las demás.
- Con los datos de mercado del 2026-08-21, el MtM coincide con el calculado desde las
  planillas (diferencia máxima 0,0006 CLP, por el redondeo a 10 decimales de las tasas en
  la base del valorizador).

## Endpoints con las planillas del correo (prefijo `/fx-options`)

| Método | Ruta | Qué hace |
|---|---|---|
| GET  | `/salud` | Estado del servicio (sin clave) |
| POST | `/mtm` | MtM y P&L no realizado de la cartera vigente (JSON) |
| POST | `/payoff` | Payoff y P&L de las operaciones que vencen (JSON) |
| POST | `/calculo` | MtM + payoff en una llamada (JSON) |
| POST | `/proceso-diario` | Genera `MtM_AAAAMMDD.xlsx`, `MtM_AAAAMMDD_vencimientos.xlsx` y `resumen_AAAAMMDD.txt`, y guarda la foto de la cartera |
| GET  | `/salidas` | Lista los informes generados |
| GET  | `/salidas/{archivo}` | Descarga un informe |

Los POST reciben `multipart/form-data` con `grid` (Grid Vol Bloomberg Creasys.xlsx),
`operaciones` (Operaciones Diarias ... al AAAA_MM_DD.xlsx) y, opcional,
`fecha_proceso` (AAAA-MM-DD). Sin fecha, se toma del nombre del archivo de
operaciones y, en último caso, del grid.

```powershell
curl.exe -F "grid=@data/Grid Vol Bloomberg Creasys.xlsx" `
         -F "operaciones=@data/Operaciones Diarias Opciones y Cartera Vigente al 2026_08_21.xlsx" `
         http://127.0.0.1:8001/fx-options/payoff
```

**Payoff al vencimiento:** CALL = máx(fixing − strike, 0), PUT = máx(strike − fixing, 0),
por el monto principal y negativo si Nevasa vende. El fixing es el USDOBS de la
cartera; si falta, se usa el spot del grid y queda en `avisos`.

**Seguimiento de cambios:** `/proceso-diario` compara la cartera vigente contra
la foto de la última fecha anterior ya procesada y avisa cambios de datos y
liquidaciones anticipadas (misma regla que el proceso diario original).

## Menú

| Opción | Qué hace |
|---|---|
| Valorización MtM | MtM de la cartera vigente por operación, con totales y descarga CSV |
| Payoff vencimientos | Liquida las operaciones que vencen en la fecha de proceso |
| Proceso diario (informes) | Ejecuta el proceso completo y descarga las planillas |
| Informes generados | Lista y descarga los informes guardados |

## Configuración (variables de entorno)

| Variable | Dónde | Uso |
|---|---|---|
| `DATABASE_URL` | API | Si existe, informes y fotos se guardan en PostgreSQL (tabla `fxo_archivos`, se crea sola). Si no, en `salidas/` y `estado/` |
| `FXO_API_KEY` | API y menú | Si existe, la API exige el encabezado `X-API-Key` y el menú lo envía |
| `FXO_SALIDAS`, `FXO_ESTADO` | API | Carpetas locales de informes y fotos (sin `DATABASE_URL`) |
| `FXO_API_URL` | Menú | URL de la API (por defecto `http://127.0.0.1:8001`; puede ser la de Heroku) |
| `FXO_DATA` | Menú | Carpeta de insumos para la opción "Carpeta data/" |
| `DATABASE_URL_TEST` | Pruebas | Base PostgreSQL exclusiva para probar el almacenamiento |

## Despliegue en Heroku

Heroku guarda en disco solo hasta el próximo reinicio del dyno, por eso en
Heroku los informes y las fotos de cartera van a Heroku Postgres.

```powershell
heroku create <nombre-app>
heroku addons:create heroku-postgresql:essential-0 -a <nombre-app>   # define DATABASE_URL
heroku config:set FXO_API_KEY=<clave-larga-aleatoria> -a <nombre-app>
git push heroku main
heroku open /fx-options/salud -a <nombre-app>
```

Para usar el menú local contra Heroku:

```powershell
$env:FXO_API_URL = "https://<nombre-app>.herokuapp.com"
$env:FXO_API_KEY = "<clave>"
streamlit run menu_mtm.py --server.port 8502
```

## Pruebas

```powershell
pytest
```

- Motor, curva, superficie y regresión de mercado contra el grid real del
  2026-07-14 (`tests/data/`), validado al centavo contra el maestro Excel.
- API: endpoints, errores, API key, payoff (incluido el reemplazo del fixing por
  spot), proceso diario con seguimiento de cambios entre dos fechas.
- Almacenamiento local y, con `DATABASE_URL_TEST`, PostgreSQL. El CI de GitHub
  Actions levanta un PostgreSQL 16 para esta prueba.
- Regresión de pipeline con la cartera real del 07-14: se salta si el archivo
  no está en `data/` (no se versiona por confidencialidad).

## Nota de seguridad

`data/`, `salidas/`, `estado/` y `.env` están en `.gitignore`: los archivos de
cartera (contrapartes, posiciones) son confidenciales y nunca deben versionarse.
Usa un repositorio privado y define siempre `FXO_API_KEY` en Heroku.
