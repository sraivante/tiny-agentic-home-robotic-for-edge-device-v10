@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto python_fallback
py -3 bootstrap.py %*
goto finished
:python_fallback
where python >nul 2>nul
if errorlevel 1 goto missing_python
python bootstrap.py %*
:finished
set "LAB_EXIT=%ERRORLEVEL%"
if not "%LAB_EXIT%"=="0" if "%~1"=="" pause
exit /b %LAB_EXIT%
:missing_python
echo Python 3.11 or newer is required. Install Python, then run this file again.
if "%~1"=="" pause
exit /b 1
