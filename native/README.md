# KerMPNative

This is the independent, fail-closed Windows x64 extension POC. It fingerprints
the loaded `Simulation_x64.dll` for Sims 4 `1.126.78.1020`, exposes bounded
status/queue APIs, and intentionally installs no hook until a concrete
Build/Buy request boundary is validated.

Supported installed fingerprint:

```text
module: Simulation_x64.dll
PE TimeDateStamp: 1785275595 (2026-07-28T21:53:15Z)
SizeOfImage: 17686528 (0x10DD000)
file version: 0001.0300.0000.0246
```

The exact game version is carried by `TS4_x64.exe` file version `1.126.78.1020`.
`Simulation_x64.dll` is the native module containing the simulation/Build-Buy
surface. Static reconnaissance has not identified a safe concrete wall-request
function, vtable, or validated signature; `hook_installed` must therefore remain
false and the module reports `capture_boundary_unvalidated`.

## Build

Use an x64 MSVC toolchain and CPython 3.7 x64 development headers/import
library matching the Sims `python37_x64.dll`. Python 3.10+ and 32-bit Python
are incompatible. From an x64 Native Tools prompt:

```powershell
pwsh -File native\build.ps1 -Python37Root C:\Python37
```

The script writes `native\KerMPNative.pyd`; copy it beside `KerMP.ts4script` in
the Mods folder. It never copies proprietary Sims binaries into the repository.

## Manual runtime flow

1. Build the native module with the command above. Build the script with
   `python3.7 sims_mod_src\build.py` (or set `PYTHON37` as documented by that
   script).
2. Copy `native\KerMPNative.pyd` and `sims_mod_src\KerMP.ts4script` to the
   active Sims Mods location and start the existing KerMP sidecar.
3. In Sims run `kermp.native.status`. On this skeleton the expected safe result
   is `native_loaded=True`, `native_game_build_supported=False`, and
   `hook_installed=False` with `last_error=unsupported_build`. Metadata is now
   diagnostic only: no historical build fingerprint authorizes a hook.
4. Enter Build Mode and draw one simple wall. Run `kermp.native.status`, then
   `kermp.native.take`. Until a boundary is validated, the queue is expected to
   remain empty and the wall must still build normally.
5. Run `kermp.build.eventprobe` before and after the edit to correlate the
   already-verified zero-argument wall notification with any future native
   capture. The callback is a timing signal only; it is not geometry.
