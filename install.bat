@ECHO OFF
REM install.bat — NanoBot setup for Windows.
REM Thin wrapper that runs install.ps1 with a relaxed execution policy so
REM double-clicking works without changing system PowerShell settings.

CHCP 65001 > NUL
CD /D "%~dp0"

WHERE powershell > NUL 2>&1
IF %ERRORLEVEL% NEQ 0 (
    ECHO ERROR: PowerShell was not found. It ships with Windows 7 and newer.
    PAUSE
    EXIT /B 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*

PAUSE
