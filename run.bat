@ECHO OFF
REM run.bat — NanoBot launcher for Windows.
REM Finds a suitable Python (3.11+), prefers a local venv, then hands off to
REM run.py (pre-flight check + launch). All arguments are passed through, so
REM `run.bat --check` works the same as `python run.py --check`.

CHCP 65001 > NUL
CD /D "%~dp0"

REM Prefer a project-local virtual environment when one exists.
IF EXIST "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" run.py %*
    GOTO end
)
IF EXIST ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py %*
    GOTO end
)

REM Try the Windows Python launcher first.
WHERE py > NUL 2>&1
IF %ERRORLEVEL% EQU 0 (
    py -3 run.py %*
    GOTO end
)

REM Fall back to python on PATH.
WHERE python > NUL 2>&1
IF %ERRORLEVEL% EQU 0 (
    python run.py %*
    GOTO end
)

ECHO ERROR: Python was not found. NanoBot requires Python 3.11 or newer.
ECHO        Run install.bat to set everything up, or install Python manually:
ECHO        https://www.python.org/downloads/
ECHO        (Make sure "Add python.exe to PATH" is checked in the installer.)

:end
PAUSE
