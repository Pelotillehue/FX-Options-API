# CLAUDE.md

Guía para Claude al trabajar en este repositorio. Complementa el README, que es la
documentación para personas.

## Resumen ejecutivo

- **Qué es:** un servicio de valorización de opciones FX europeas USD/CLP. Calcula el MtM, las
  griegas (Delta spot, Gamma spot y Vega) y el payoff al vencimiento.
- **Para qué:** es el motor de FX Options del Valorizador de Derivados
  (`D:\Valorizador Derivados`), cuya app le envía las opciones y el mercado y suma el resultado
  a la cartera. También reemplaza el proceso diario en Excel/VBA: recibe las dos planillas del
  correo diario y genera los informes de MtM y de vencimientos.
- **Por qué es confiable:** el motor replica el modelo VBA de la planilla maestra `MtM.xlsm` y
  está validado al centavo contra ella con el mercado del 2026-07-14. Con datos en JSON desde
  el valorizador, la diferencia máxima contra las planillas es de 0,0006 CLP (redondeo de tasas
  a 10 decimales en la base del valorizador).
- **Cómo se usa:** API REST (FastAPI) en el puerto 8001 en local y en Heroku (`fx-options-api`),
  más un menú Streamlit propio para el proceso con planillas.
- **Alcance actual:** sólo USD/CLP y ejercicio europeo. No guarda operaciones ni mercado: los
  recibe en cada llamada. Sólo persiste los informes y las fotos de cartera del proceso diario.

## Resumen técnico

### Dos modos de uso

| Modo | Entrada | Endpoints | Quién lo usa |
|---|---|---|---|
| JSON | Operaciones y mercado en el cuerpo | `/fx-options/valorizacion`, `/fx-options/liquidacion` | App del Valorizador de Derivados (`app/comun/fx_options_api.py`) |
| Planillas | `multipart/form-data` con `grid` y `operaciones` (.xlsx) y `fecha_proceso` opcional | `/fx-options/mtm`, `/payoff`, `/calculo`, `/proceso-diario`, `/salidas`, `/salidas/{archivo}` | Menú `menu_mtm.py` y `curl` |

`GET /fx-options/salud` no exige clave y devuelve la versión (`VERSION` en `api.py`, hoy 1.1.0).

### Módulos

Todos en la raíz, sin paquete; los tests agregan la raíz al `sys.path` (`tests/conftest.py`).

- `api.py`: FastAPI (`api:app`). Esquemas Pydantic de los endpoints JSON, manejo de adjuntos en
  carpeta temporal, clave por `X-API-Key` y traducción de errores de insumos a HTTP 422.
- `servicio_mtm.py`: cálculo como datos, sin escribir archivos. Con planillas:
  `valorizar_cartera`, `calcular_payoff` y `calcular_todo`. Con JSON: `mercado_desde_datos`,
  `valorizar_operaciones` y `liquidar_operaciones`.
- `fx_options_valuation.py`: motor. `MarketData`, `VolSurface`, `Operacion`, `mtm_option_fx`,
  `griegas_option_fx` y `liquidar_vencimiento`. Sin dependencias externas.
- `load_market.py`: lee `Grid Vol Bloomberg Creasys.xlsx` (hojas `FWD pts DEPOs` y `Mids BGN`).
  `completar_fila_curva` completa la tasa CLP (promedio bid/ask) y la tasa USD implícita.
- `daily_process.py`: proceso diario con planillas: lee la hoja `Operaciones Diarias`, clasifica
  vigentes, vencimientos y altas, valoriza, liquida, calcula P&L, detecta cambios contra la foto
  anterior y escribe `MtM_AAAAMMDD.xlsx`, `MtM_AAAAMMDD_vencimientos.xlsx` y
  `resumen_AAAAMMDD.txt`. También se ejecuta solo:
  `python daily_process.py <grid.xlsx> <operaciones.xlsx> [AAAA-MM-DD] [dir_salida]`.
- `almacenamiento.py`: informes (`salida`) y fotos de cartera (`estado`). `AlmacenLocal` usa
  `salidas/` y `estado/`; `AlmacenPostgres`, la tabla `fxo_archivos`, que se crea sola. Se elige
  con `DATABASE_URL`.
- `dateparse.py`: fechas en varios formatos, incluido el español `vie 09-ene-26`.
- `menu_mtm.py`: menú Streamlit, cliente de la API; no importa el motor.

### Modelo de valorización

- Garman-Kohlhagen sobre forward: prima = FD_CLP × (F × N(d1) − K × N(d2)) para call, por el
  nominal, negativa si la institución vende.
- Curvas: interpolación lineal de tasas por días, plana fuera de rango (`lin_interpol`).
  Descuento lineal Act/360: FD = 1 / (1 + r × días / 360). Forward = spot × FD_USD / FD_CLP.
  La curva CLP usa todos los nodos con tasa CLP; la USD, sólo los que traen tasa USD.
- Tiempo de la opción: días / 365.
- Superficie: por plazo, volatilidades de put 10, put 25, ATM, call 25 y call 10 delta; los
  strikes salen de la delta spot sin ajuste por prima, con FD_USD lineal Act/360. `get_vol`
  interpola primero por plazo (volatilidades y strikes) y después por strike, plana fuera de
  rango.
- Plazos de la superficie en días: D = 1, W = 7, M = 30, Y = 365 (1M = 30, 18M = 540, 2Y = 730).
- Griegas, con FD_USD = FD_CLP × forward / spot y t = días / 365: delta call = FD_USD × N(d1),
  delta put = FD_USD × (N(d1) − 1), gamma = FD_USD × N'(d1) / (spot × σ × √t) y
  vega = spot × FD_USD × √t × N'(d1). En monto, con signo desde la institución (compra suma,
  venta resta): `delta_usd` = nocional × delta, `gamma_usd_1pct` = nocional × gamma × spot × 1% y
  `vega_clp_1pt` = nocional × vega × 1%. Sticky strike.
- Payoff: CALL máx(fixing − strike, 0), PUT máx(strike − fixing, 0), por el nominal, negativo si
  la institución vende. Con planillas, el fixing es el USDOBS de la cartera; si falta, el spot
  del grid, y queda en `avisos`.
- Unidades: el motor trabaja en porcentaje (tasas y volatilidades); los endpoints JSON reciben
  y devuelven tanto por uno y `servicio_mtm.py` convierte.

### Stack

Python 3.13; FastAPI, Pydantic 2, uvicorn y python-multipart; openpyxl y Pillow para las
planillas; psycopg 3 sólo si hay `DATABASE_URL`; Streamlit y requests en el menú; pytest y httpx
en las pruebas. CI en GitHub Actions (`.github/workflows/tests.yml`) con PostgreSQL 16, sobre
`master` y los pull requests.

## Contrato con el Valorizador de Derivados

- La app del valorizador (`D:\Valorizador Derivados\app\comun\fx_options_api.py`) es el cliente
  de los endpoints JSON. Envía tal cual los objetos de su API: `operaciones` de `GET /fx-options`,
  `spot` y `nodos` de `GET /mercado/cotizaciones-swap/IRS_CLPCAM` y `puntos` de
  `GET /mercado/volatilidades/USDCLP`. Por eso los esquemas heredan de `_Flexible`
  (`extra="ignore"`): no cambiarlo a `forbid`.
- Campos que se leen: de la operación, `deal_number`, `posicion` (`COMPRA` o `VENTA`),
  `tipo_opcion` (`CALL` o `PUT`), `nocional`, `strike`, `fecha_vencimiento`, `fecha_negociacion`,
  `moneda_principal`, `moneda_secundaria` y `tipo_ejercicio`; de la curva, `dias`, `tasa_clp` y
  `tasa_usd`; de la superficie, `plazo`, `atm` y call/put de 25 y 10 delta o, si faltan, RR y BF
  (call = ATM + RR/2 + BF, put = ATM − RR/2 + BF).
- La selección de operaciones vigentes la hace quien llama. Una operación que no se puede
  valorizar (par distinto de USD/CLP, no europea, tipo o posición inválidos, ya vencida) vuelve
  con `mtm` nulo y el motivo en `error`, sin detener las demás.
- La respuesta de `/valorizacion` trae por operación `dias`, `forward`, `volatilidad`,
  `factor_descuento`, `mtm_clp`, `mtm`, las griegas unitarias (`delta`, `gamma`, `vega`) y en
  monto (`delta_usd`, `gamma_usd_1pct`, `vega_clp_1pt`) y `error`; y los totales `mtm_total`,
  `delta_usd_total`, `gamma_usd_1pct_total`, `vega_clp_1pt_total`, `valorizadas`, `con_error` y
  `avisos`. `moneda` puede ser `CLP` o `USD` (MtM en CLP dividido por el spot); las griegas no
  dependen de ella.
- La respuesta de `/liquidacion` trae por operación `fixing`, `itm`, `payoff_clp` y `error`, y
  los totales `payoff_neto_clp`, `recibido_clp`, `pagado_clp` e `itm`.
- La app del valorizador exige la versión 1.1.0 o posterior para las griegas. No quitar ni
  renombrar campos de las respuestas; al agregar uno, subir `VERSION` y documentarlo en el
  README, y actualizar el cliente del valorizador en el mismo cambio si lo usa.
- `completar_fila_curva` también es la lógica con que la API del valorizador completa la tabla
  IRS CLPCAM: si cambia aquí, debe cambiar allá.

## Comandos

```powershell
cd "D:\FX Options API"
.\.venv\Scripts\Activate.ps1                      # Python 3.13
pip install -r requirements-dev.txt               # API + menú + pruebas
uvicorn api:app --reload --port 8001              # API: http://127.0.0.1:8001/docs
streamlit run menu_mtm.py --server.port 8502      # menú: http://localhost:8502
pytest                                            # todas las pruebas
python daily_process.py <grid.xlsx> <operaciones.xlsx> [AAAA-MM-DD] [dir_salida]
```

`iniciar.bat` levanta la API y el menú. Los puertos 8001 y 8502 evitan chocar con el
Valorizador de Derivados (API en 8000 y app en 8501).

## Configuración

| Variable | Dónde | Uso |
|---|---|---|
| `DATABASE_URL` | API | Si existe, informes y fotos van a PostgreSQL (`fxo_archivos`); si no, a `salidas/` y `estado/` |
| `FXO_API_KEY` | API y menú | Si existe, todos los endpoints salvo `/fx-options/salud` exigen `X-API-Key` |
| `FXO_SALIDAS`, `FXO_ESTADO` | API | Carpetas locales de informes y fotos |
| `FXO_API_URL` | Menú | Dirección de la API (por defecto `http://127.0.0.1:8001`) |
| `FXO_DATA` | Menú | Carpeta de insumos |
| `DATABASE_URL_TEST` | Pruebas | Base PostgreSQL exclusiva para probar el almacenamiento |

En el Valorizador de Derivados la clave se llama `FX_OPTIONS_API_KEY` y la dirección,
`FX_OPTIONS_API_URL`; la clave debe tener el mismo valor que `FXO_API_KEY`.

## Convenciones

- Todo en español: mensajes, documentación y archivos generados, con tildes, diéresis y eñes
  correctas. Los identificadores van sin tildes.
- Docstrings normales de Python, no Doxygen (a diferencia del Valorizador de Derivados).
- El motor (`fx_options_valuation.py`) replica el VBA de la planilla maestra: no cambiar
  convenciones (Act/360, Act/365, interpolación, strikes desde delta) sin que sigan pasando las
  regresiones de `tests/test_market_regression.py` y `tests/test_regression.py`. Lo que se agregue
  fuera del VBA original (como las griegas) se indica en el docstring del módulo.
- `servicio_mtm.py` no escribe archivos ni guarda estado; sólo `/proceso-diario` persiste.
- Los endpoints JSON reciben y devuelven tanto por uno; el motor, porcentaje.
- Cada endpoint o campo nuevo lleva su prueba en `tests/` y su descripción en el README.
- `requirements.txt` es lo que instala Heroku: lo que sólo usan el menú o las pruebas va en
  `requirements-menu.txt` o `requirements-dev.txt`.
- Archivos Excel generados: sin letras cursivas.
- Commits con la línea `Co-Authored-By: Claude <noreply@anthropic.com>`.

## Reglas de negocio

- Operación vigente a una fecha: `inicio <= fecha < vencimiento`. La cartera del día se
  reconstruye desde la planilla de operaciones, sin estado acumulado.
- Fecha de proceso con planillas, en orden: el argumento `fecha_proceso`, la del nombre del
  archivo de operaciones (`... al AAAA_MM_DD.xlsx`) y, en último caso, la del grid.
- Vencen las operaciones con vencimiento igual a la fecha de proceso; no se valorizan, se
  liquidan.
- Seguimiento de cambios: `/proceso-diario` guarda la foto `estado_cartera_AAAAMMDD.json` y la
  compara con la de la última fecha anterior ya procesada; avisa cambios de datos y
  liquidaciones anticipadas.
- P&L no realizado: prima más MtM, sólo si la operación trae prima. El tipo de cambio de la
  prima y el calce entre operaciones se resuelven en `daily_process.py` (`_fx_prima`,
  `_fx_por_calce`).
- Tasas fuera de −2% a 25% no se filtran: se usan y quedan en `avisos`.
- Los nombres de archivo de `/salidas/{archivo}` se validan sin rutas (`almacenamiento._validar`).

## Pruebas

`pytest`, en `tests/`: motor y griegas (`test_valuation.py`), regresión de mercado contra el
grid del 2026-07-14 en `tests/data/` (`test_market_regression.py`), carga del grid
(`test_load_market.py`), fechas (`test_dateparse.py`), proceso diario y seguimiento de cambios
(`test_daily_process.py`), endpoints de planillas, errores y clave (`test_api.py`), endpoints
JSON (`test_api_json.py`) y almacenamiento local y PostgreSQL (`test_almacenamiento.py`, este
último sólo con `DATABASE_URL_TEST`). `test_regression.py` usa la cartera real del 07-14 y se
salta si el archivo no está en `data/`.

## Datos y seguridad

- `data/`, `salidas/`, `estado/` y `.env` están en `.gitignore`: las planillas de cartera traen
  contrapartes y posiciones reales y nunca se versionan. También se ignoran todos los `.xlsx`,
  `.xlsm` y `.csv`, salvo `tests/data/*.xlsx`, que es sólo mercado.
- No copiar nombres de contrapartes ni folios reales a pruebas, documentación ni ejemplos; los
  ejemplos usan datos inventados.
- Nunca commitear `FXO_API_KEY` ni `DATABASE_URL`. En Heroku se define siempre `FXO_API_KEY`.

## Despliegue

Heroku, app `fx-options-api` (remoto Git `heroku`), con Heroku Postgres para los informes y las
fotos, porque el disco del dyno se pierde en cada reinicio. `Procfile`:
`web: uvicorn api:app --host 0.0.0.0 --port $PORT`; `.python-version` fija 3.13. Se despliega
desde `master` (`git push heroku master`). GitHub: `Pelotillehue/FX-Options-API` (remoto
`origin`). El menú Streamlit no se despliega: se usa en el PC, apuntando a la API con
`FXO_API_URL`.

Si un cambio agrega campos que la app del valorizador necesita, se despliega primero esta API
y después `valorizador-app`.

## Cómo responder al usuario

Respuestas directas en español, sin preámbulos ni resúmenes de lo dicho, sin ofrecer pasos
siguientes salvo que los pida. Si algo es interpretación y no dato, decirlo al inicio. Cuando
pregunta si algo se puede hacer, responder eso, sin implementarlo.
