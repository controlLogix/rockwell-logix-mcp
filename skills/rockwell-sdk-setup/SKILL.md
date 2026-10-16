---
name: rockwell-sdk-setup
description: >-
  Set up and troubleshoot the Rockwell Logix Designer SDK (LDSDK) for Python automation on
  Windows, including the rockwell_local MCP environment. Use this whenever standing up, repairing,
  or debugging a Logix Designer / Studio 5000 automation environment — pip installing the SDK,
  pythonnet / coreclr "Failed to create a .NET runtime" errors, a 32-bit FtspAdapterLDSDK.exe host
  failure (HRESULT 0x800700C1), DOTNET_ROOT problems, LdSdkService, FactoryTalk activation, or an
  "operation has timed out" on open/create/build. Use it even when the user does not name the SDK
  but is clearly wiring up Logix Designer automation, the Rockwell MCP, or Python-drives-Studio-5000.
---

# Rockwell Logix Designer SDK — environment setup

The Logix Designer SDK (LDSDK) lets Python/.NET drive an **installed** Studio 5000 Logix Designer.
It is **Windows-only** and talks to a local `LdSdkService` (gRPC) which drives Logix Designer. Most
setup pain is not the SDK itself — it is the **.NET runtime wiring**, because the SDK mixes a
64-bit Python process with a **32-bit** helper executable. Get the two `DOTNET_ROOT` variables
right and everything else falls into place.

## Prerequisites (verify first)
- **Studio 5000 Logix Designer** installed (v31 min; many ops need v37/v38). `build` needs v37+.
- **Logix Designer SDK** installed (ships the Python wheel and the native `LdSdkService`).
- **Python 3.12+** (64-bit). Check `python --version`; some installs use `py` instead of `python`.
- `LdSdkService` running: `Get-Service LdSdkService` → Status `Running` (it hosts gRPC, default
  port 53204, and runs as `NT AUTHORITY\LocalService`).

## Install the Python packages
The wheel ships with the SDK, typically at
`C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-*.whl`.

```powershell
python -m pip install "C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl"
# comms/telemetry + MCP stack used by rockwell_local:
python -m pip install mcp pycomm3 pylogix asyncua pymodbus paho-mqtt
```
Installing the wheel pulls `pythonnet` + `clr_loader` (the .NET bridge) and `numpy`.

## The two DOTNET_ROOT variables (this is the crux)
The SDK does **two** .NET things, and they use **different architectures**:
1. In-process, 64-bit Python loads the CSClient .NET assembly via `pythonnet`/`clr_loader`.
2. For project operations (open/create/build) it spawns a **32-bit** helper,
   `...\site-packages\logix_designer_sdk\_assemblies\FtspAdapterLDSDK.exe`.

`clr_loader` looks for the runtime at `C:\Program Files\dotnet\shared`, but modern .NET installs
put the runtime under an **architecture subfolder**: `...\dotnet\x64` and `...\(x86)\dotnet`. So set
**both**, machine-wide, pointing at the real roots:

```powershell
[Environment]::SetEnvironmentVariable("DOTNET_ROOT",      "C:\Program Files\dotnet\x64",       "Machine")
[Environment]::SetEnvironmentVariable("DOTNET_ROOT(x86)", "C:\Program Files (x86)\dotnet",     "Machine")
```
- `DOTNET_ROOT` → where **64-bit** Python finds the runtime. Confirm `...\x64\shared\Microsoft.NETCore.App` exists.
- `DOTNET_ROOT(x86)` → where the **32-bit** `FtspAdapterLDSDK.exe` finds its runtime. Confirm `...(x86)\dotnet\shared\Microsoft.NETCore.App` exists.

Setting only `DOTNET_ROOT` (to the x64 path) makes the **32-bit** adapter try to load the **x64**
`hostfxr.dll` and fail with `HRESULT 0x800700C1` ("Ensure the library matches the current process
architecture: x86") — which surfaces to Python as `LogixSdkError: The operation has timed out`,
because the adapter crashed and the call never completes. Set `DOTNET_ROOT(x86)` and the timeout
disappears. Note: the parentheses are part of the variable name; quote it in cmd (`set "DOTNET_ROOT(x86)=..."`).

Any launcher (e.g. the rockwell_local `run-mcp.cmd`) and any ad-hoc script should set both, so the
environment is correct regardless of whether the machine-wide vars have propagated.

## Verify no x86 runtime? Install one
If `C:\Program Files (x86)\dotnet\shared\Microsoft.NETCore.App` is missing, install the **x86**
.NET Desktop/Runtime that matches the adapter (commonly .NET 8 x86) before setting `DOTNET_ROOT(x86)`.

## Smoke test (prove the bridge, then prove project ops)
```python
import os, asyncio
os.environ.setdefault("DOTNET_ROOT", r"C:\Program Files\dotnet\x64")
os.environ["DOTNET_ROOT(x86)"] = r"C:\Program Files (x86)\dotnet"
from logix_designer_sdk.logix_project import LogixProject
async def go():
    # 1) in-process bridge (no adapter): should return ~100 catalog numbers
    print("procs:", len(list(LogixProject.get_processor_types(38))))
    # 2) full path (spawns the 32-bit adapter): create -> build -> export L5X
    p = await LogixProject.create_new_project(r"C:\Temp\smoke.ACD", 38, "1756-L71", "Smoke")
    await p.build(); await p.save_as(r"C:\Temp\smoke.L5X", True, True); await p.close()
    print("project ops OK")
asyncio.run(go())
```
`get_processor_types` working but `create/open/build` timing out is the signature of the
`DOTNET_ROOT(x86)` problem above — the lighter call runs in-process; the heavier ones spawn the adapter.

## Failure signatures
| Symptom | Cause | Fix |
|---|---|---|
| `Failed to create a .NET runtime (coreclr)`, path `...dotnet\shared` not found | 64-bit Python can't find the runtime | Set `DOTNET_ROOT` to `...\dotnet\x64` |
| `get_processor_types` works, `create/open/build` → `operation has timed out`; event log shows `FtspAdapterLDSDK.exe` `HRESULT 0x800700C1` | 32-bit adapter loading the x64 host | Set `DOTNET_ROOT(x86)` to `...(x86)\dotnet` |
| All project ops hang | `LdSdkService` not running | Start it; set to Automatic |
| Online download/activation errors | No FactoryTalk activation (`.lic`) | Offline create/build/export still work; online download to hardware needs an activation — resolve in FactoryTalk Activation Manager |

## Activation caveat
Offline project work (create, build, export, L5X, offline tag values) runs **without** a FactoryTalk
activation. **Online download to physical hardware** generally needs a valid Studio 5000 activation —
check `C:\Users\Public\Documents\Rockwell\Activations\*.lic` and resolve before any hardware phase.

## When done
Confirm: both `DOTNET_ROOT` vars set machine-wide, `LdSdkService` Running, the smoke test's
`create → build → export` all pass. Then the environment is ready for the `rockwell_local` MCP
(see the `rockwell-logix-mcp` skill for operating it).
