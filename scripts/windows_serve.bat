@echo off
REM ============================================================
REM  اجرای سرور پروداکشن روی ویندوز با Waitress
REM  Production launcher (self-contained):
REM    * Ensures a strong DJANGO_SECRET_KEY exists in .env
REM      (generates and saves one automatically if missing).
REM    * Runs with DJANGO_DEBUG=False.
REM    * Applies migrations and collects static files.
REM    * Serves the app with Waitress (gunicorn does NOT run on Windows).
REM  The database credentials come from .env (see scripts\setup_env.bat).
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run scripts\windows_setup.bat first.
  goto :end
)
call ".venv\Scripts\activate.bat"

if not exist ".env" (
  echo [WARNING] .env not found. Run scripts\setup_env.bat first to set the database.
)

REM --- Ensure a strong DJANGO_SECRET_KEY is stored in .env (no setx needed) ---
findstr /b /c:"DJANGO_SECRET_KEY=" ".env" >nul 2>&1
if errorlevel 1 (
  for /f "delims=" %%K in ('python -c "import secrets;print(secrets.token_urlsafe(64))"') do set "GENKEY=%%K"
  >> ".env" echo DJANGO_SECRET_KEY=!GENKEY!
  echo [secret] Generated a new DJANGO_SECRET_KEY and saved it to .env
) else (
  echo [secret] DJANGO_SECRET_KEY already present in .env
)

REM --- Production settings for this run ---
set "DJANGO_DEBUG=False"
if "%DJANGO_ALLOWED_HOSTS%"=="" set "DJANGO_ALLOWED_HOSTS=planning.poliran,localhost,127.0.0.1"
if "%DJANGO_CSRF_TRUSTED_ORIGINS%"=="" set "DJANGO_CSRF_TRUSTED_ORIGINS=http://planning.poliran:8000,http://planning.poliran"

echo [1/3] Applying migrations ...
python manage.py migrate --noinput
if errorlevel 1 goto :end

echo [2/3] Collecting static files ...
python manage.py collectstatic --noinput
if errorlevel 1 goto :end

echo ============================================================
echo   PRODUCTION MODE  -  Waitress (DEBUG off)
echo ============================================================
echo [3/3] Starting Waitress on http://0.0.0.0:8000/   (LAN: http://planning.poliran:8000/)
waitress-serve --listen=0.0.0.0:8000 erp.wsgi:application

:end
endlocal
