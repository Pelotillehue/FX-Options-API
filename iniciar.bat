@echo off
REM Inicia la API de FX Options (puerto 8001) y el menu de MtM (puerto 8501).
REM Cierra las dos ventanas para detenerlos.
cd /d "%~dp0"
start "API FX Options" ".venv\Scripts\python.exe" -m uvicorn api:app --reload --port 8001
timeout /t 3 /nobreak >nul
start "Menu MtM FX Options" ".venv\Scripts\python.exe" -m streamlit run menu_mtm.py --server.port 8502
