@echo off
if exist "%~dp0..\.node\npm.cmd" (
  set "PATH=%~dp0..\.node;%PATH%"
  call "%~dp0..\.node\npm.cmd" %*
) else (
  call npm.cmd %*
)

