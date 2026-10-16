@echo off
setlocal
rem rockwell-logix-mcp launcher: set the two .NET roots, then start the stdio server.
rem Adjust the paths below to your machine's .NET runtime locations if different.
set "DOTNET_ROOT=C:\Program Files\dotnet\x64"
set "DOTNET_ROOT(x86)=C:\Program Files (x86)\dotnet"
python "%~dp0server.py"
