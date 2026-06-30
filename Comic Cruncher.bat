@echo off
cd /d "%~dp0"
pythonw comic_cruncher.py
if %errorlevel% neq 0 (
    python comic_cruncher.py
    pause
)
