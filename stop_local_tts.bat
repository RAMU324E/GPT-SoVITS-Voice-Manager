@echo off
chcp 65001>nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python local_tts_service.py stop
pause
