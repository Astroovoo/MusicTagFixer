@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [MusicTagFixer] launching GUI...
echo Working dir: %CD%
echo.

set "PY_EXE="

if exist "..\.venv\Scripts\python.exe" (
  "..\.venv\Scripts\python.exe" -c "import tkinter" >nul 2>nul && set "PY_EXE=..\.venv\Scripts\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python37-32\python.exe" (
  "%LocalAppData%\Programs\Python\Python37-32\python.exe" -c "import tkinter" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python37-32\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
  "%LocalAppData%\Programs\Python\Python311\python.exe" -c "import tkinter" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
  "%LocalAppData%\Programs\Python\Python310\python.exe" -c "import tkinter" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python310\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python39\python.exe" (
  "%LocalAppData%\Programs\Python\Python39\python.exe" -c "import tkinter" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python39\python.exe"
)

if not defined PY_EXE if exist "%LocalAppData%\Programs\Python\Python38\python.exe" (
  "%LocalAppData%\Programs\Python\Python38\python.exe" -c "import tkinter" >nul 2>nul && set "PY_EXE=%LocalAppData%\Programs\Python\Python38\python.exe"
)

if not defined PY_EXE (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python -c "import tkinter" >nul 2>nul && set "PY_EXE=python"
  )
)

if not defined PY_EXE (
  echo ERROR: No usable Python with tkinter found.
  echo.
  echo Install Python from python.org and keep tcl/tk selected.
  echo.
  pause
  exit /b 1
)

echo Using Python: %PY_EXE%

if not exist "mp3_tag_gui.py" (
  echo ERROR: mp3_tag_gui.py not found in %CD%
  echo.
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

%PY_EXE% "mp3_tag_gui.py"
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo GUI exited with code %EXIT_CODE%.
  echo If traceback appears above, send it to me.
  pause
)

exit /b %EXIT_CODE%
