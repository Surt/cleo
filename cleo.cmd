@echo off
:: cleo — Claude ecosystem dependency manager (Windows)
:: Usage: cleo <subcommand> [args...]
:: Add the directory containing this file to your PATH, e.g.:
::   setx PATH "%PATH%;C:\path\to\cleo"
set CLEO_ROOT=%~dp0
python3 "%CLEO_ROOT%tools\cleo.py" %*
