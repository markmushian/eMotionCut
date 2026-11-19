@echo off
setlocal
cd /d "%~dp0"

rem Troubleshooting launcher: runs in the foreground so every message stays
rem visible. Keep this file pure ASCII with CRLF line endings, like the normal
rem launcher.
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="
set "NO_PROXY="
set "http_proxy="
set "https_proxy="
set "all_proxy="
set "no_proxy="
set "ELECTRON_RUN_AS_NODE="
set "NODE_OPTIONS="

set "APP=%~dp0node_modules\electron\dist\electron.exe"
echo [eMotionCut] app : %APP%
echo [eMotionCut] cwd : %CD%
if not exist "%APP%" (
  echo [eMotionCut] Electron is not installed. Run "npm ci" here.
  pause
  exit /b 1
)
echo [eMotionCut] starting, log follows
echo ---------------------------------------------------------------
"%APP%" .
set "CODE=%ERRORLEVEL%"
echo ---------------------------------------------------------------
echo [eMotionCut] exited with code %CODE%
pause
exit /b %CODE%
