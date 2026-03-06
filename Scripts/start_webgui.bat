@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PORT=8766"

echo [MusicTagFixer] launching Web GUI...
echo Working dir: %CD%
echo Port: %PORT%
echo.

set "PY_EXE="

if not defined PY_EXE if exist "..\.venv\Scripts\python.exe" (
  "..\.venv\Scripts\python.exe" -c "import sys" >nul 2>nul && set "PY_EXE=..\.venv\Scripts\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
  "%LocalAppData%\Programs\Python\Python311\python.exe" -c "import sys" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
  "%LocalAppData%\Programs\Python\Python310\python.exe" -c "import sys" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python310\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python39\python.exe" (
  "%LocalAppData%\Programs\Python\Python39\python.exe" -c "import sys" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python39\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python38\python.exe" (
  "%LocalAppData%\Programs\Python\Python38\python.exe" -c "import sys" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python38\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python37-32\python.exe" (
  "%LocalAppData%\Programs\Python\Python37-32\python.exe" -c "import sys" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python37-32\python.exe"
)

if not defined PY_EXE (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python -c "import sys" >nul 2>nul && set "PY_EXE=python"
  )
)

if not defined PY_EXE (
  where py >nul 2>nul
  if %errorlevel%==0 (
    py -3 -c "import sys" >nul 2>nul && set "PY_EXE=py -3"
  )
)

if not defined PY_EXE (
  echo ERROR: No usable Python found.
  echo Please install Python 3 and retry.
  pause
  exit /b 1
)

echo Using Python: %PY_EXE%

if not exist "mp3_tag_webgui.py" (
  echo ERROR: mp3_tag_webgui.py not found in %CD%
  pause
  exit /b 1
)

%PY_EXE% -c "import mutagen" >nul 2>nul
if errorlevel 1 (
  echo Installing mutagen via %PY_EXE% ...
  %PY_EXE% -m pip install mutagen
  if errorlevel 1 (
    echo ERROR: failed to install mutagen.
    pause
    exit /b 1
  )
)

echo Opening http://127.0.0.1:%PORT%/
start "" "http://127.0.0.1:%PORT%/"

%PY_EXE% "mp3_tag_webgui.py" --host 127.0.0.1 --port %PORT%
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Web GUI exited with code %EXIT_CODE%.
  pause
)

exit /b %EXIT_CODE%
