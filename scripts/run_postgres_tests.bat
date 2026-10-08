@echo off
setlocal

if not "%ERP_POSTGRES_TESTS%"=="1" (echo [ERROR] ERP_POSTGRES_TESTS must equal 1.& exit /b 2)
if not "%POSTGRES_TEST_DB_NAME%"=="erp_test_db" (echo [ERROR] POSTGRES_TEST_DB_NAME must be exactly erp_test_db.& exit /b 2)
echo(%POSTGRES_TEST_DB_NAME%| findstr /I /C:"test" >nul || (echo [ERROR] POSTGRES_TEST_DB_NAME must be exactly erp_test_db.& exit /b 2)
if /I "%POSTGRES_TEST_DB_NAME%"=="erp_db" (echo [ERROR] POSTGRES_TEST_DB_NAME must be exactly erp_test_db.& exit /b 2)
if not "%POSTGRES_TEST_DB_USER%"=="erp_test_user" (echo [ERROR] POSTGRES_TEST_DB_USER must be exactly erp_test_user.& exit /b 2)
if "%POSTGRES_TEST_DB_PASSWORD%"=="" (echo [ERROR] POSTGRES_TEST_DB_PASSWORD is required.& exit /b 2)
if "%POSTGRES_TEST_DB_HOST%"=="" (echo [ERROR] POSTGRES_TEST_DB_HOST is required.& exit /b 2)
if "%POSTGRES_TEST_DB_PORT%"=="" (echo [ERROR] POSTGRES_TEST_DB_PORT is required.& exit /b 2)
if not exist ".venv\Scripts\python.exe" (echo [ERROR] Project virtual environment Python was not found.& exit /b 2)

set "TEST_LABELS=planning.tests_postgresql_reservations planning.tests_postgresql_program_governance planning.tests_postgresql_final_merge planning.tests_postgresql_preproduction_revision production.tests_postgresql_execution production.tests_postgresql_target_revision production.tests_postgresql_lifecycle"
if "%~1"=="" goto :run
if /I "%~1"=="planning" set "TEST_LABELS=planning.tests_postgresql_reservations planning.tests_postgresql_program_governance planning.tests_postgresql_final_merge planning.tests_postgresql_preproduction_revision"& goto :run
if /I "%~1"=="production" set "TEST_LABELS=production.tests_postgresql_execution production.tests_postgresql_target_revision production.tests_postgresql_lifecycle"& goto :run
echo [ERROR] Unknown mode. Use planning, production, or no argument.
exit /b 2

:run
".venv\Scripts\python.exe" manage.py test %TEST_LABELS% --settings=erp.settings_postgres_tests --keepdb --verbosity 2
exit /b %ERRORLEVEL%
