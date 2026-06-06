@echo off
REM Build TookBot Dev Launcher en .exe single-file via PyInstaller
REM Prerequis : pip install pyinstaller

cd /d "%~dp0"

echo === Verif PyInstaller ===
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller absent, installation...
    python -m pip install pyinstaller
)

echo === Build dev_launcher.exe ===
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "TookBot Dev Launcher" ^
    --clean ^
    dev_launcher.py

if exist "dist\TookBot Dev Launcher.exe" (
    echo.
    echo === Build OK ===
    echo Fichier : %CD%\dist\TookBot Dev Launcher.exe
    echo Double-clic pour lancer.
) else (
    echo.
    echo === Build FAILED ===
)

pause
