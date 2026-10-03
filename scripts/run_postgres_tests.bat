@echo off
setlocal

if not "%ERP_POSTGRES_TESTS%"=="1" goto :bad_optin
if not "%POSTGRES_TEST_DB_NAME%"=="erp_test_db" goto :bad_database
echo(%POSTGRES_TEST_DB_NAME%| findstr /I /C:"test" >nul || goto :bad_database
if /I "%POSTGRES_TEST_DB_NAME%"=="erp_db" goto :bad_database
if not "%POSTGRES_TEST_DB_USER%"=="erp_test_user" goto :bad_user
if "%POSTGRES_TEST_DB_PASSWORD%"=="" goto :bad_password
if "%POSTGRES_TEST_DB_HOST%"=="" goto :bad_host
if "%POSTGRES_TEST_DB_PORT%"=="" goto :bad_port
if not exist ".venv\Scripts\python.exe" goto :bad_python

set "TEST_LABELS=planning.tests_postgresql_reservations production.tests_postgresql_execution"
if "%~1"=="" goto :run
if /I "%~1"=="planning" set "TEST_LABELS=planning.tests_postgresql_reservations"& goto :run
if /I "%~1"=="production" set "TEST_LABELS=production.tests_postgresql_execution"& goto :run
echo [ERROR] Unknown mode. Use planning, production, or no argument.
exit /b 2

:run
".venv\Scripts\python.exe" manage.py test %TEST_LABELS% --settings=erp.settings_postgres_tests --keepdb --verbosity 2
exit /b %ERRORLEVEL%

:bad_optin
echo [ERROR] ERP_POSTGRES_TESTS must equal 1.
exit /b 2
:bad_database
echo [ERROR] POSTGRES_TEST_DB_NAME must be exactly erp_test_db.
exit /b 2
:bad_user
echo [ERROR] POSTGRES_TEST_DB_USER must be exactly erp_test_user.
exit /b 2
:bad_password
echo [ERROR] POSTGRES_TEST_DB_PASSWORD is required.
exit /b 2
:bad_host
echo [ERROR] POSTGRES_TEST_DB_HOST is required.
exit /b 2
:bad_port
echo [ERROR] POSTGRES_TEST_DB_PORT is required.
exit /b 2
:bad_python
echo [ERROR] Project virtual environment Python was not found.
exit /b 2
