@echo off
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem Why this file is pure ASCII:
rem cmd.exe parses a .cmd using the console ANSI codepage, not UTF-8. A UTF-8
rem script with Chinese characters in it gets mis-decoded, and any non-ASCII
rem characters in a path corrupt the rest of the line.
rem ---------------------------------------------------------------------------

rem Windows refuses to create a process when the environment block holds the
rem same key twice in different cases (HTTP_PROXY plus http_proxy). A stray
rem ELECTRON_RUN_AS_NODE also turns Electron into a headless node process.
rem Clear them here so the launcher works whatever opened it.
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

rem The desktop service is started as Electron in node mode, so no system Node
rem is required. Add the portable Node 24 only if it is present, for the tools
rem that shell out to it. Resolved relative to this file, so no path literal.
for %%I in ("%~dp0..\..\tools\node24") do if exist "%%~fI\node.exe" set "PATH=%%~fI;%PATH%"

set "APP=%~dp0node_modules\electron\dist\electron.exe"
if not exist "%APP%" (
  echo [eMotionCut] Electron is not installed.
  echo Run "npm ci" in this folder first.
  pause
  exit /b 1
)

if not exist "%~dp0.dev" mkdir "%~dp0.dev"

rem "start" hands the process to the shell, so it survives this window closing.
rem /d sets the working directory, which must be this folder.
start "" /d "%~dp0" "%APP%" .
exit /b 0
