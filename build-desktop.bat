@echo off
setlocal
cd /d "%~dp0" || exit /b 1

set "lembarklik_build_python=.venv\Scripts\python.exe"
if not exist "%lembarklik_build_python%" (
    echo Jalankan setup.bat terlebih dahulu.
    pause
    exit /b 1
)

"%lembarklik_build_python%" -I -c "import struct, platform; raise SystemExit(struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64'))"
if errorlevel 1 (
    echo Build Windows x64 membutuhkan Python x64.
    pause
    exit /b 1
)

"%lembarklik_build_python%" -I -m pip install -r requirements-desktop.txt
if errorlevel 1 goto failed

"%lembarklik_build_python%" -I -m PyInstaller --noconfirm --clean --onedir --windowed --name LembarKlik ^
    --add-data "lembarklik\web:lembarklik/web" ^
    --collect-all pypdfium2_raw --collect-data webview --collect-data pdfminer ^
    --collect-data docx --collect-data pptx --collect-data openpyxl desktop.py
if errorlevel 1 goto failed

if not exist "dist\LembarKlik\LembarKlik.exe" goto failed
powershell.exe -NoProfile -Command "Compress-Archive -Path 'dist/LembarKlik' -DestinationPath 'dist/LembarKlik-Windows-x64.zip' -Force"
if errorlevel 1 goto failed
echo Build selesai. Buka dist\LembarKlik\LembarKlik.exe untuk mengetesnya.
echo Paket distribusi ada di dist\LembarKlik-Windows-x64.zip.
pause
exit /b 0

:failed
echo Build gagal. Periksa pesan di atas.
pause
exit /b 1
