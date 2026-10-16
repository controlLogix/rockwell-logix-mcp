# Building a Rockwell PLC product from a SOO — the proven workflow

How to turn a **Sequence of Operations (SOO)** into a deployed, telemetry-publishing Rockwell
(CompactLogix/GuardLogix, Studio 5000 v38) PLC program — **ST-first**, code-generated as L5X, and
validated by the real Logix compiler before download. This is a repeatable method; the bundled
`examples/build_chiller.py` is the worked reference (a chiller sim that publishes MQTT over the CIP
socket interface).

> Read alongside `rockwell-st-codegen.md` (the ST/AOI/socket codegen rules) in this skill, and
> `examples/build_chiller.py` in the repo.

## Toolchain

- **`rockwell_local` MCP** (this repo's `server.py`) + **`l5xgen.py`** run on the Windows host where
  the Logix Designer SDK is installed. Set both env vars before invoking the SDK:
  `DOTNET_ROOT` (x64, for the Python process) and `DOTNET_ROOT(x86)` (for the 32-bit adapter the SDK
  spawns).
- **Studio 5000 Logix Designer v38**; a FactoryTalk activation (trial is fine) is needed for live
  online/download, not for offline create/build/export.
- Treat `examples/build_chiller.py` as the template: UDT + control AOI + JSON AOI + MQTT/socket comms
  + MainProgram, all emitted via `l5xgen.inject(...)`.

## The workflow

### 1. SOO → point list → UDT
Extract every I/O point, status flag, setpoint, mode, and alarm the SOO names. Model the equipment as
one `UDT_<Equipment>` (BOOL/DINT/REAL members) — the contract the rest hangs off.
`l5xgen.datatype_l5x(name, members)`.

### 2. Control logic → ST AOI(s)
Translate the SOO's sequence into an ST **state machine** in an AOI: start/stop/permissives,
interlocks, capacity/PID modulation, alarm evaluation + fault latch. One AOI per piece of equipment
(instanced per unit). `l5xgen.aoi_l5x(...)` with `st_lines=[...]`. Pass the equipment UDT as an
**InOut** parameter; keep Input/Output params atomic. For a simulator, fold a light-physics plant
model into the same AOI so points move coherently; for real I/O, map UDT members to module tags.

### 3. Telemetry → JSON AOI + MQTT comms (reusable building blocks)
From `examples/build_chiller.py`:
- **`AOI_JsonBuild`** (+ `AOI_RealToStr`) — serialize the UDT to a **compact grouped JSON** payload.
  Keep the whole MQTT packet **≤ 462 bytes** (the unconnected WriteSocket cap).
- **`AOI_MqttConnect` / `AOI_MqttPublish`** — assemble MQTT 3.1.1 CONNECT (user/pass) and PUBLISH
  (QoS0; retained = `16#31`) packets into `SINT` byte buffers.
- **Socket client** — a `CASE` state machine at **program scope** (MESSAGE tags can't be AOI locals)
  doing Create → Open → Write(CONNECT) → Read(CONNACK) → Write(PUBLISH loop) → Delete over CIP
  Generic `MSG` to the socket object `0x342`. These comms/serialization AOIs are product-agnostic —
  reuse them for any equipment's telemetry. Topic convention: `bms/<equipment>/<id>/telemetry`.

### 4. Wire the MainProgram / task
Call the AOIs in order (control → JSON → MQTT assemble → socket machine). Put comms on a **PERIODIC
task** (~1000 ms) for a sane publish cadence (post-process the generated L5X `Type="CONTINUOUS"` →
`Type="PERIODIC" Rate="1000.0"` for the new task).

### 5. Generate + validate
`l5xgen.inject(base, udts=[...], aois=[...], programs=[...], schedule=[...])` splices into a bare
SDK-exported base L5X. Then **`validate_l5x(out, build=True)`** — opens + **compiles** with the real
Logix compiler. **An L5X imports even when its ST won't compile, so always gate on `build_ok`,** never
on import. Read the `RxSTX_*`/`XMLSrv_*` error, fix, repeat.

### 6. Deploy safely (preserve Safety + the controller's IP)
On a **GuardLogix safety controller**, never recreate the project from scratch (you'd wipe its Safety
program and could change its IP). Instead:
1. `upload_to_new_project(acd, comm_path, confirm=True)` → a copy of what's on the controller.
2. Build your program as a **renamed** program + its own task on a bare base, `save_as` ACD,
   `partial_export_xml` the program (`Controller/Programs/Program[@Name='X']`) and its task.
3. `partial_import_xml` those fragments into a **copy of the uploaded ACD** (import to the containers
   `Controller/Programs`, `Controller/Tasks`), `save_project`, `build_project`.
4. Download (does NOT disturb Safety, the module tree, or the IP): `set_comm_path` →
   `change_controller_mode PROGRAM` → **`download` (works only while Offline, controller in PROGRAM)**
   → `change_controller_mode RUN`. Comm path: `AB_ETHIP-1\<controller-ip>\Backplane\<slot>`.

### 7. Verify
- Tags: **`read_logix_telemetry(ip, ["Program:<Prog>.<tag>"])`** (pycomm3). Prefer this over SDK
  `go_online`, which fails to correlate when the project has offline changes or an offline child
  module (`RxCL_E_CANNOT_UPLOAD_PHYS_ADDR`). MESSAGE sub-members read fine (`.ERR`, `.EXERR`,
  `.Instance`, `.DN`).
- MQTT: `mosquitto_sub -h <broker> -u <user> -P <pass> -t 'bms/#' -v`. To discover topics, subscribe
  to `#` (MQTT has no list-topics call).

## The hard-won rules (detail in `rockwell-st-codegen.md`)

- **ST:** string literals only in `:=` (stage them for `CONCAT`/`COP`); BOOL never in a numeric
  expression (branch to 1/0); AOI Input/Output params must be atomic (UDT/STRING/array ⇒ InOut); AOI
  calls are positional `AOIName(Backing, InOut/required…)` with optional inputs set as instance members.
- **AOI:** `MESSAGE` tags can't be AOI locals (import aborts) → socket/MSG comms live at program scope.
- **CIP sockets (the 4 fixes):** ConnectionPath `"1, 0"` + ConnectedFlag `"1"` (= unconnected; the
  importer rejects `"0"`); MOV the created instance into each later MSG's writable `.Instance`; set
  `msgWrite.REQ_LEN := 16 + BufLen` before every WriteSocket (else `16#0015` "too much data"); front
  Ethernet-port sockets must be unconnected.
- **MQTT in ST:** fixed header `16#30` (or `16#31` retained), remaining-length varint via
  `REPEAT…UNTIL`, 2-byte topic length, `COP` topic/payload into the buffer, store any byte > 127 as
  `byte - 256` into the `SINT`.

## Safety & secrets

- Never modify the **SafetyTask/SafetyProgram**; build + compile **offline** first; get explicit
  per-action confirmation before any download/mode-change to a live safety controller.
- Keep MQTT credentials in **config tags** (set once in the routine, not scattered); configure your own
  broker address and controller IP/slot before downloading to your hardware.
