@echo off
REM ============================================================
REM  نصب و آماده‌سازی سامانه روی ویندوز ۱۰ (یک‌بار اجرا شود)
REM  Setup script for Windows 10. Requires Python 3.12 (py launcher).
REM ============================================================
setlocal
cd /d "%~dp0.."

REM PostgreSQL is the primary database. Before running this, make sure the
REM database exists and set these environment variables (never hard-code the
REM password here) — see .env.example:
REM   DB_ENGINE DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT

echo [1/5] Creating virtual environment (.venv) ...
py -3 -m venv .venv
if errorlevel 1 (
  echo Could not create venv. Make sure Python 3.12+ is installed and "py" is on PATH.
  exit /b 1
)

call ".venv\Scripts\activate.bat"

echo [2/5] Upgrading pip and installing dependencies ...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo [3/5] Applying database migrations ...
python manage.py migrate --noinput
if errorlevel 1 exit /b 1

echo [4/5] Seeding demo data (idempotent) ...
python manage.py seed_demo

echo [5/5] Done.
echo.
echo Development server : scripts\windows_run.bat
echo Production server  : scripts\windows_serve.bat
endlocal
