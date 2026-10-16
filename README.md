# rockwell-logix-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes Rockwell Automation's **Logix Designer
SDK** as 39 tools for programmatic ControlLogix / CompactLogix engineering — plus six skills that
document how to use it well. Create and build Studio 5000 projects, generate L5X from a points list,
read/write tags, resolve comm paths and (safely, confirm-gated) go online / download, publish or
simulate telemetry over any protocol, and run FAT/SAT acceptance suites.

> **Unofficial / community project.** Not affiliated with or endorsed by Rockwell Automation.
> "Logix Designer", "Studio 5000", "ControlLogix", "CompactLogix" are trademarks of Rockwell
> Automation. You must hold your own Studio 5000 and Logix Designer SDK licenses.

## What's here
- `server.py` — the MCP server (Python `mcp` 2.x, stdio transport); 39 tools.
- `l5xgen.py` — pure-Python L5X code generation using a **clone-and-splice** approach.
- `telemetry.py` — any-protocol telemetry: simulate, publish (MQTT, OPC UA), live read (EtherNet/IP).
- `fatsat.py` — FAT/SAT assertion operators + report formatting.
- `skills/` — six skills: `rockwell-sdk-setup`, `rockwell-logix-mcp`, `rockwell-l5x-codegen`,
  `rockwell-build-commission`, `rockwell-telemetry`, `rockwell-fat-sat`.

## Prerequisites (you provide these)
This server drives an **installed** Rockwell toolchain; it does not bundle it.
- **Windows** with **Studio 5000 Logix Designer v37+** and the **Logix Designer SDK 2.02+**
  installed. The SDK's `LdSdkService` must be running (gRPC, default port 53204).
- **Python 3.12+ (64-bit)**.
- The LDSDK Python wheel from your SDK installation (not redistributed here):
  ```
  pip install "C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python\logix_designer_sdk-2.0.2-py3-none-any.whl"
  ```
- The rest: `pip install -r requirements.txt`

### The two DOTNET_ROOT variables (the #1 setup gotcha)
The SDK loads .NET in-process (64-bit) **and** spawns a 32-bit helper (`FtspAdapterLDSDK.exe`), so
set **both** machine-wide, pointing at the real runtime roots (modern .NET installs use an
architecture subfolder, not `dotnet\shared`):
```
setx DOTNET_ROOT "C:\Program Files\dotnet\x64" /M
setx "DOTNET_ROOT(x86)" "C:\Program Files (x86)\dotnet" /M
```
Set only `DOTNET_ROOT` and the 32-bit helper tries to load the 64-bit host and dies with
`0x800700C1`, which surfaces in Python as `LogixSdkError: The operation has timed out`. The
`rockwell-sdk-setup` skill has the full troubleshooting guide.

## Run
`run-mcp.cmd` sets the env vars and starts the server over stdio. Register it with your MCP client as
a stdio server (command: the path to `run-mcp.cmd`); the tools appear after the client reconnects.

## Tools by area
- **Lifecycle / build:** `create_project`, `open_project`, `convert_project`, `save_project`,
  `save_project_as`, `export_l5x`, `close_project`, `change_controller_type`, `build_project`,
  `get_processor_types`, `list_open_projects`, `server_info`.
- **L5X:** `partial_export_xml`, `partial_import_xml`, `partial_import_rungs`, `parse_l5x`
  (enumerate — the SDK can't list tags), `generate_project_from_points`, `validate_l5x`, `diff_l5x`.
- **Tags:** `read_tag`, `write_tag` (OFFLINE project values or ONLINE live; bare names auto-resolve
  to the tag's XPath).
- **Comms / online (confirm-gated):** `build_comm_path`, `set_comm_path`, `get_comm_path`,
  `read_connected_state`, `read_controller_mode`, `go_online`, `go_offline`,
  `change_controller_mode`, `download`, `upload`, `upload_to_new_project`.
- **Telemetry:** `simulate_telemetry`, `telemetry_selftest` (in-process OPC UA round-trip, no
  hardware), `publish_telemetry_mqtt`, `read_logix_telemetry`.
- **FAT/SAT:** `run_fat_suite`, `run_fat_suite_telemetry`, `controller_health`.

## Safety
Operations that change a controller — `download`, `change_controller_mode`, online writes,
`go_online`, `upload` — are **confirm-gated**: without `confirm=true` they return a dry-run plan and
do nothing. Always dry-run, confirm the exact target (IP + slot), then execute. A download overwrites
the controller's running program; a move to RUN energizes outputs and actuates equipment.

## How code generation works
`l5xgen` clones a bare, valid project (exported from the SDK) as a template and splices in generated
`<DataType>`, `<Tag>`, and `<Program>` elements — so the result compiles, without hand-authoring the
large controller/module boilerplate. Prove any generated project with `validate_l5x`, which opens and
compiles it. See the `rockwell-l5x-codegen` skill.

## License
MIT — see [LICENSE](LICENSE).
