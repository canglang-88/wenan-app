@echo off
setlocal
set "APP_DIR=%~dp0"
set "EXE=%APP_DIR%文案中枢.exe"
set "SCRIPT=%APP_DIR%app.py"
set "PYTHONW="

if exist "%EXE%" (
    start "" "%EXE%"
    exit /b
)

if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PYTHONW if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not defined PYTHONW if exist "%ProgramFiles%\Python312\pythonw.exe" set "PYTHONW=%ProgramFiles%\Python312\pythonw.exe"
if not defined PYTHONW if exist "%ProgramFiles%\Python311\pythonw.exe" set "PYTHONW=%ProgramFiles%\Python311\pythonw.exe"

if defined PYTHONW (
    start "" "%PYTHONW%" "%SCRIPT%"
    exit /b
)

where pyw >nul 2>nul
if not errorlevel 1 (
    start "" pyw -3 "%SCRIPT%"
    exit /b
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "%SCRIPT%"
    exit /b
)

where py >nul 2>nul
if not errorlevel 1 (
    start "" py -3 "%SCRIPT%"
    exit /b
)

echo 未找到 Python 或 pythonw.exe。
echo 请先安装 Python 3.11 或 3.12，然后重新打开本程序。
pause
