@echo off
setlocal
cd /d "%~dp0"
REM Keep this launcher ASCII with CRLF for Windows cmd code pages.
REM Use windowless Python and release the BAT console immediately.
REM Application startup errors are recorded in crash.log.
set "PYTHONPATH=%CD%\.deps;%CD%\src;%PYTHONPATH%"
if exist "C:\Python313\pythonw.exe" (
  start "" /b "C:\Python313\pythonw.exe" -m macroflow.ui.app >nul 2>&1
) else (
  start "" /b pythonw -m macroflow.ui.app >nul 2>&1
)
endlocal
exit /b
