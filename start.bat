@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw fortnite_counter.py
) else (
    echo Python не найден. Установите Python 3.9+ с https://www.python.org/downloads/
    echo При установке отметьте "Add python.exe to PATH".
    pause
)
 