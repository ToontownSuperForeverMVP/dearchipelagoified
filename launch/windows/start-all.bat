@echo off
title Toontown Archipelago: Unified Launcher
setlocal EnableExtensions

set "WIN32_DIR=%~dp0"

rem --- Resolve ppython.exe ---
set "PPYTHON_PATH="
if exist "%WIN32_DIR%PPYTHON_PATH" (
    set /P PPYTHON_PATH=<"%WIN32_DIR%PPYTHON_PATH"
)
if not defined PPYTHON_PATH (
    set "PPYTHON_PATH="%WIN32_DIR%..\..\Panda3D\python\ppython.exe""
)
if not exist %PPYTHON_PATH% (
    echo Could not find ppython.exe.
    echo - Checked: %WIN32_DIR%PPYTHON_PATH
    echo - Fallback: %WIN32_DIR%..\..\Panda3D\python\ppython.exe
    pause
    exit /b 1
)

rem --- Run the unified launcher script (starts Astron, UberDOG, AI, and Client) ---
%PPYTHON_PATH% "%WIN32_DIR%launcher.py"

exit /b %errorlevel%
