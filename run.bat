@echo off
setlocal
cd /d "%~dp0"
REM Keep this launcher ASCII with CRLF for Windows cmd code pages.
REM Run source with the project dependencies and Python 3.13 when installed.
set "PYTHONPATH=%CD%\.deps;%CD%\src;%PYTHONPATH%"
if exist "C:\Python313\python.exe" (
  "C:\Python313\python.exe" -m macroflow.ui.app
) else (
  python -m macroflow.ui.app
)
if errorlevel 1 pause
endlocal
