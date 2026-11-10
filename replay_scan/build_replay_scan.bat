@echo off
chcp 65001 >nul
title Building replay_scan
cd /d "%~dp0"

where dotnet >nul 2>nul
if errorlevel 1 (
  echo Не найден .NET 10 SDK. Скачай ".NET SDK" для Windows x64 на странице dotnet.microsoft.com/download/dotnet/10.0
  pause
  exit /b 1
)

dotnet publish src\ReplayScan\ReplayScan.csproj -c Release -r win-x64 --self-contained true ^
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true ^
  -p:EnableCompressionInSingleFile=true -p:DebugType=none -o dist_scan
if errorlevel 1 (
  echo Сборка не удалась, смотри сообщения выше.
  pause
  exit /b 1
)

echo.
echo Проверка на твоём реплее:  dist_scan\replay_scan.exe "путь\к\файлу.replay"
echo Готово: dist_scan\replay_scan.exe - положи его рядом с Build.bat питоновской программы
pause
