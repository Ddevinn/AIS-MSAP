@echo off
setlocal
call "%~dp0start_dev.bat" %*
if errorlevel 1 pause
