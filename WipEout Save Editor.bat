@echo off
rem WipEout Omega Save Editor - Copyright (c) 2026 Synapz3 - MIT License
rem Starts the editor without a console window. Needs Python 3.7+ with Tkinter (see README.md).
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw "%~dp0wipeout_save_editor.py" %* & exit /b)
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0wipeout_save_editor.py" %* & exit /b)
where python >nul 2>nul && (python "%~dp0wipeout_save_editor.py" %* & if errorlevel 1 pause & exit /b)
where py >nul 2>nul && (py -3 "%~dp0wipeout_save_editor.py" %* & if errorlevel 1 pause & exit /b)
echo.
echo  Python was not found.
echo.
echo  The WipEout Omega Save Editor needs Python 3.7 or newer (with Tkinter).
echo   1. Download it from https://www.python.org/downloads/windows/
echo   2. Run the installer: tick "Add python.exe to PATH" and keep "tcl/tk and IDLE" ticked.
echo      (or, in a terminal:  winget install Python.Python.3.13 )
echo   3. Start this file again.
echo.
pause
