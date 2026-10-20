@echo off
rem Run the rb command from this checkout. Needs Python 3.11 or later on PATH.
set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
python -m responseboard %*
