param(
    [Parameter(Mandatory=$true)] [string] $Python37Root
)
$ErrorActionPreference = "Stop"
$include = Join-Path $Python37Root "include"
$lib = Join-Path $Python37Root "libs\python37.lib"
if (!(Test-Path (Join-Path $include "Python.h"))) { throw "Python 3.7 x64 headers not found: $include\Python.h" }
if (!(Test-Path $lib)) { throw "Python 3.7 x64 import library not found: $lib" }
if (!(Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw "Run from an x64 Native Tools command prompt so cl.exe is available." }
$out = Join-Path $PSScriptRoot "KerMPNative.pyd"
cl.exe /nologo /std:c++17 /EHsc /LD /O2 /DMS_WIN64 /I"$include" "$PSScriptRoot\KerMPNative.cpp" /link /LIBPATH:"$(Join-Path $Python37Root 'libs')" python37.lib /OUT:"$out" /PDB:"$out.pdb" /IMPLIB:"$out.lib"
Write-Host "Built $out"
