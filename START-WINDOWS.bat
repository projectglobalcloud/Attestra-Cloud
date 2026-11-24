@echo off
rem Starts the Attestra platform on this Windows computer.
rem
rem   Double-click this file, or run it from Command Prompt.
rem
rem It finds Python 3.10 or newer and hands over to start.py, which does the
rem rest. If no suitable Python is installed, it downloads a private copy into
rem this folder (nothing is installed system-wide, no administrator rights).

setlocal
title Attestra
cd /d "%~dp0"
if errorlevel 1 goto :nofolder

set "PY="

rem The Python launcher "py" comes with every python.org installation.
for %%V in (3.13 3.12 3.11 3.10 3.14) do call :try_py %%V
if defined PY goto :run

rem A plain "python" on the PATH. The Microsoft Store placeholder fails this
rem check harmlessly, so it is skipped.
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :run

goto :use_uv

:try_py
if defined PY goto :eof
py -%1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -%1"
goto :eof

:run
%PY% start.py %*
if errorlevel 1 goto :failed
goto :end

:use_uv
rem No suitable Python: fetch uv (a small, self-contained Python manager) into
rem .tools\ and let it provide Python 3.12 for this project only.
set "UV="
if exist ".tools\uv.exe" set "UV=.tools\uv.exe"
if exist ".tools\bin\uv.exe" set "UV=.tools\bin\uv.exe"
if defined UV goto :run_uv

echo.
echo   Python 3.10 or newer was not found on this computer.
echo   Downloading a private copy for this project (one time only)...
if not exist ".tools" mkdir ".tools"
set "UV_INSTALL_DIR=%CD%\.tools"
set "UV_NO_MODIFY_PATH=1"
set "INSTALLER_NO_MODIFY_PATH=1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
if exist ".tools\uv.exe" set "UV=.tools\uv.exe"
if exist ".tools\bin\uv.exe" set "UV=.tools\bin\uv.exe"
if defined UV goto :run_uv
echo.
echo   The download failed. Check the internet connection, or install Python
echo   from https://www.python.org/downloads/ (tick "Add python.exe to PATH")
echo   and run this again.
goto :failed

:run_uv
"%UV%" run --no-project --python 3.12 start.py %*
if errorlevel 1 goto :failed
goto :end

:nofolder
echo   Could not open the project folder.
goto :failed

:failed
echo.
pause
endlocal
exit /b 1

:end
endlocal
exit /b 0
