@echo off
setlocal
cd /d "%~dp0" || exit /b 1

call :find_python
if not errorlevel 1 goto setup

where winget >nul 2>&1
if errorlevel 1 goto python_help
echo Python 3.11 or newer was not found. Installing Python 3.13 with WinGet...
winget install --id Python.Python.3.13 --exact --source winget --scope user --silent
if errorlevel 1 echo WinGet did not report a successful installation. Checking again for Python...
call :find_python
if errorlevel 1 goto python_help

:setup
if defined lembarklik_existing_venv goto dependencies
echo Creating the LembarKlik Python environment...
"%lembarklik_python%" %lembarklik_python_args% -I -m venv ".venv"
if errorlevel 1 goto venv_failed

:dependencies
echo Checking LembarKlik dependencies...
".venv\Scripts\python.exe" -I -m pip --disable-pip-version-check install -r requirements.txt
if errorlevel 1 goto dependencies_failed

call :check_office
if not errorlevel 1 goto office_ready
echo.
echo LibreOffice enables Word, Excel, and PowerPoint to PDF.
echo You can add it later by running setup.bat again.
choice /c YN /n /m "Install LibreOffice now? [Y/N] "
if errorlevel 2 goto complete
if not errorlevel 1 goto complete
where winget >nul 2>&1
if errorlevel 1 goto office_help
winget install --id TheDocumentFoundation.LibreOffice --exact --source winget
call :check_office
if errorlevel 1 goto office_help

:office_ready
echo LibreOffice detected. Office-to-PDF conversion is available.
goto complete

:office_help
echo LibreOffice was not detected. You can install it manually later:
echo https://www.libreoffice.org/download/download-libreoffice/

:complete
echo Setup complete. Open run.bat to launch LembarKlik.
pause
exit /b 0

:check_office
".venv\Scripts\python.exe" -E -s -c "from lembarklik.local_programs import programs; raise SystemExit(programs()['office'] is None)" >nul 2>&1
exit /b %errorlevel%

:find_python
set "lembarklik_existing_venv="
call :use_python ".venv\Scripts\python.exe"
if not errorlevel 1 (
    set "lembarklik_existing_venv=1"
    exit /b 0
)
call :use_python "py" -3.11
if not errorlevel 1 exit /b 0
call :use_python "py" -3
if not errorlevel 1 exit /b 0
call :use_python "python"
if not errorlevel 1 exit /b 0
rem Find the per-user installation even when this window still has the old PATH.
for /d %%P in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    call :use_python "%%~fP\python.exe"
    if not errorlevel 1 exit /b 0
)
exit /b 1

:use_python
"%~1" %~2 -I -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 exit /b 1
set "lembarklik_python=%~1"
set "lembarklik_python_args=%~2"
exit /b 0

:python_help
echo Could not find or install Python 3.11 or newer.
echo Install Python manually from https://www.python.org/downloads/windows/
echo or enable WinGet through Microsoft App Installer, then rerun setup.bat.
pause
exit /b 1

:venv_failed
echo Could not create the Python environment. Check that this folder is writable.
pause
exit /b 1

:dependencies_failed
echo Could not install LembarKlik dependencies. Check the output above and your internet connection, then rerun setup.bat.
pause
exit /b 1
