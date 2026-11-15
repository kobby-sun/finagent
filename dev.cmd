@echo off
rem Windows Command Prompt shortcut: dev start | dev stop | dev load FIN-001 ...
if exist ".venv\Scripts\python.exe" (set PY=.venv\Scripts\python.exe) else (set PY=py)
%PY% "%~dp0scripts\dev\devctl.py" %*
