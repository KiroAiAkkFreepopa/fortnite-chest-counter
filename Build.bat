@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден. Установите Python 3.9+ с https://www.python.org/downloads/
    echo При установке отметьте "Add python.exe to PATH".
    pause
    exit /b 1
)
if not exist chest_map.png (
    echo Нет chest_map.png рядом с Build.bat
    pause
    exit /b 1
)
if not exist map_ref.png (
    echo Нет map_ref.png рядом с Build.bat
    pause
    exit /b 1
)
if not exist replay_scan.exe (
    echo Нет replay_scan.exe рядом с Build.bat.
    echo Собери его через build_replay_scan.bat и скопируй из dist_scan сюда.
    pause
    exit /b 1
)
rem Ставим только то, чего не хватает. Уже установленные пакеты не трогаем:
rem если программа запущена, Windows держит cv2.pyd и переустановка падает с "Отказано в доступе".
python -c "import cv2, numpy, PIL, mss" >nul 2>nul
if errorlevel 1 (
    python -m pip install pillow numpy opencv-python mss
    if errorlevel 1 goto fail
)
python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    python -m pip install pyinstaller
    if errorlevel 1 goto fail
)
set "ICON="
if exist FortniteChestCounter.ico set "ICON=--icon FortniteChestCounter.ico"
python -m PyInstaller --noconfirm --clean --onefile --windowed %ICON% --name FortniteChestCounter --add-data "chest_map.png;." --add-data "map_ref.png;." --add-data "replay_scan.exe;." fortnite_counter.py
if errorlevel 1 goto fail
echo.
echo Готово: dist\FortniteChestCounter.exe  (один файл: карты и replay_scan.exe внутри)
pause
exit /b 0
:fail
echo Сборка не удалась, смотри сообщения выше.
pause
exit /b 1