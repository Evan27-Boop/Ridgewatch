@echo off
rem Starts weather-tui from any folder: weather [options]
where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0weather.py" %*
) else (
    python "%~dp0weather.py" %*
)
