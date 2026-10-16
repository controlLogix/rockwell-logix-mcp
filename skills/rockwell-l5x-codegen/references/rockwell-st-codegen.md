# Rockwell Structured Text codegen reference (for L5X generation)

Distilled, **validated** rules for generating compilable Structured Text + Add-On Instructions as
L5X on ControlLogix/CompactLogix (5380/5580, v38). Sources: Rockwell **1756-PM007** (Logix 5000
Structured Text Programming), **1756-RM018** (General Instructions Reference), **1756-PM001** (Common
Procedures) — PDFs in `~/Downloads` — plus compile-tested against the live 5069-L306ERS2. Every rule
below was confirmed by `validate_l5x(build=True)` (the Logix ST compiler), not just by import.

> **The golden rule:** an L5X **imports** even when its ST will **not compile**. Always gate on a
> real build (`validate_l5x(build=True)` / `build_project`), never on import success.

## Structured Text

### Assignment & expressions (PM007)
- `tag := expression;` — tag is BOOL, SINT, INT, DINT, REAL, or **STRING** (STRING assignment is
  5380/5580 only; our 5380 qualifies). `[:=]` is the non-retentive form.
- Expressions may contain: tag names, immediate numbers, **string literals**, functions
  (`ABS`, `TRUNC`, `SIN`, `COS`, …), and operators (`+ - * ** / MOD`, relational, `AND/OR/NOT`,
  bitwise). Keywords are case-insensitive.
- **BOOL never appears in a numeric expression.** `d := Chiller.Running;` (DINT := BOOL) fails with
  `RxSTX_E_BOOLOPND_UNEXPECTED`. Branch it: `IF Chiller.Running THEN d := 1; ELSE d := 0; END_IF;`.
- Use `1`/`0` for BOOL literals (the reference AOIs do). `IF boolTag THEN …` is fine as a condition.

### Strings (the big one)
- **String literals use single quotes:** `'text'`. A double quote is an ordinary character inside
  them (`'"'`). Escapes use `$`: `$'` (quote), `$R$L` (CRLF), `$0A` (hex), `$$` (dollar), `''` (empty).
- **String literals are valid only in assignments/expressions, NOT as string-instruction operands.**
  `CONCAT(a, '.', dest)` fails with `RxSTX_E_UNEXPECTED_TOKEN`. Stage the literal first:
  `lit := '.'; CONCAT(a, lit, dest);`.
- **Append/build** with the ASCII string instructions, tag operands only: `CONCAT(srcA, srcB, dest);`
  (append), `INSERT`, `DELETE`, `FIND`, `MID`. In-place append `CONCAT(buf, piece, buf);` is the
  idiom. String copy is plain assignment: `dest := src;`.
- **No native REAL→STRING** (`RTOS` does not exist). Build it: `scaled := TRUNC(In*100.0);` →
  `whole := scaled/100; frac := scaled MOD 100;` → `DTOS(whole, sW); DTOS(frac, sF);` → CONCAT with a
  staged `'.'` (see `AOI_RealToStr` in `../chiller/build_chiller.py`). `DTOS` (DINT→STRING) and
  `STOD` (STRING→DINT) do work in ST.
- Built-in `STRING` is 82 chars. For larger buffers (JSON) define a string type (below).
- Instruction calls in ST are `INSTR(operand1, operand2, …);` (e.g. `DTOS(d, s);`).

### Constructs (PM007)
`IF…THEN…ELSIF…ELSE…END_IF;`, `CASE…OF…ELSE…END_CASE;`, `FOR…DO…END_FOR;`, `WHILE…DO…END_WHILE;`,
`REPEAT…UNTIL…END_REPEAT;`. Comments: `//` line and `(* … *)` block.

## Add-On Instructions (AOIs)

### Parameters (PM001 / RM)
- Every AOI auto-includes system params **EnableIn** (Input) and **EnableOut** (Output) first.
- **Input and Output parameters must be ATOMIC** (BOOL/SINT/INT/DINT/LINT/REAL). A structure,
  **STRING, or array can only be an InOut parameter** — otherwise the L5X import is rejected
  (`XMLSrv_E_IMPORT_ABORTED_NO_CHANGES`). Pass the equipment UDT / a string buffer as **InOut**.
- Atomic Input/Output params take a `<DefaultData Format="Decorated">`; InOut params do not.
- The AOI's main routine is named **`Logic`**. `ExecuteEnableInFalse="false"` means no EnableInFalse
  routine is needed.

### Calling an AOI (from ST or ladder)
- **Positional**, by the AOI's **definition name**, with the **backing (instance) tag first**:
  `AOIName(BackingTag, operand1, operand2, …);` (e.g. `T_Sun(SunCalcs, Now, Sunrise, Sunset);`).
- Operands are the **InOut params and any Required params**, in definition order. **Optional (Required
  =false) input params are NOT operands** — set them as instance members before the call:
  `Inst.Enable := 1; Inst.Setpoint := 44.0; AOIName(Inst, InOutTag);`. Passing them as operands gives
  `RxSTX_E_LESS_OPERANDS - Too many arguments`.
- Named-argument form (`param := value`) is **not** accepted for AOI calls — positional only.
- An AOI may hold another AOI as a **local tag** and call it (nested AOIs), e.g. `AOI_JsonBuild`
  holds `rts : AOI_RealToStr` and calls `AOI_RealToStr(rts, In, Out);`.

## L5X structure (what the generator emits)
- **ST routine:** `<Routine Name="X" Type="ST"><STContent><Line Number="n"><![CDATA[ … ]]></Line>…</STContent></Routine>`.
- **AOI:** `<AddOnInstructionDefinition Name= Revision= ExecuteEnableInFalse= …><Parameters>…</Parameters><LocalTags>…</LocalTags><Routines><Routine Name="Logic" Type="ST">…</Routine></Routines></AddOnInstructionDefinition>`. It belongs in the `<AddOnInstructionDefinitions>` container (after `<Modules>`, before controller `<Tags>`). Define referenced UDTs/string types first.
- **String type:** `<DataType Name="STRING_512" Family="StringFamily" Class="User"><Members><Member Name="LEN" DataType="DINT" .../><Member Name="DATA" DataType="SINT" Dimension="512" Radix="ASCII" .../></Members></DataType>`.
- These are emitted by `l5xgen.py`: `st_routine_l5x`, `aoi_l5x` (+ `aoi_param_l5x`, `aoi_localtag_l5x`),
  `datatype_l5x(family="StringFamily", …)`, and `inject(aois=[…])`.

## Workflow
Generate → `validate_l5x(build=True)` → read the `RxSTX_*` / `XMLSrv_*` error → fix → repeat. The
error codes are specific: `UNEXPECTED_TOKEN` (syntax, often a literal where a tag is required),
`BOOLOPND_UNEXPECTED` (BOOL in a numeric expression), `LESS_OPERANDS` (wrong AOI operand count),
`IMPORT_ABORTED` (structural/parameter-type violation, caught at import before build).

## EtherNet/IP Socket Interface (ENET-AT002) — for MQTT/raw TCP

Logix has no TCP/MQTT instruction; you drive sockets with **CIP Generic MSG** instructions to the
**socket object, class `16#0342`, Attribute 0**, over an **unconnected** MSG on the **"Message to
Self"** path (the MSG targets the controller's own Ethernet). 5380/5580 fw 35+ support **32 socket
instances**; TLS is a separate "secure socket" option.

**Services (ServiceCode hex, all Class `0x342`):**
| Service | Code | Instance | Source (request) | Destination (response) |
|---|---|---|---|---|
| Socket Create | `0x4B` | `0` | `CreateParams{Type DINT (1=TCP,2=UDP); Addr{Family INT=2; Port INT (0=any); Addr DINT (0=any)}}` = 12 B | `DINT` = socket **instance #** |
| OpenConnection | `0x4C` | the socket inst | `{Timeout DINT (ms); DestAddr STRING}`; DestAddr is `"ip?port=n"` (e.g. `192.0.2.10?port=1883`); SrcLen = 8 + addr chars | none |
| WriteSocket | `0x4E` | the socket inst | `{Timeout DINT; ToAddr{Family INT; Port INT; Addr DINT} (all 0 for TCP); BufLen DINT; Buf SINT[n]}`; SrcLen = 16 + BufLen | `DINT` = bytes written |
| ReadSocket | `0x4D` | the socket inst | `{Timeout DINT; BufLen DINT}` = 8 B | `{FromAddr{Family INT; Port INT; Addr DINT}; BufLen DINT; Buf SINT[n]}` (TCP FromAddr=0) |
| DeleteSocket | `0x4F` | the socket inst | none | none |
| AcceptConnection | `0x50` | the socket inst | `DINT` Timeout | `{Instance DINT; Addr{Family INT; Port INT; Addr DINT}}` |

Buffers: standard connection max **SINT[484]** read / **SINT[472]** write (large: 3984/3972).

**TCP client flow (what MQTT uses):** CreateSocket (Type=1 TCP) → get instance → **MOV instance into
each subsequent MSG's `.Instance`** → OpenConnection(`"host?port=1883"`) → WriteSocket(MQTT CONNECT
bytes) → ReadSocket(CONNACK) → WriteSocket(PUBLISH) → periodic WriteSocket(PINGREQ) → DeleteSocket.

**MESSAGE tags CANNOT be AOI local tags** (confirmed: a single MESSAGE local tag aborts the AOI
import with `XMLSrv_E_IMPORT_ABORTED`). So socket comms cannot be wrapped in an AOI — the
`MSG`/`MESSAGE` must live at **program (or controller) scope**. The standard pattern: keep the
packet assembly (MQTT CONNECT/PUBLISH byte buffer) in an AOI, but put the socket **state machine +
MESSAGE tags + source/dest UDT tags in a program ST routine** that consumes the AOI-built buffer.
`l5xgen.tag_l5x(name, "MESSAGE", message={...})` emits a program-scope MESSAGE tag; the chiller
`build_chiller.py` wires Create→Open→Write→Delete as a `CASE sockState` machine in MainRoutine.

**MSG L5X config** (harvested): a `MESSAGE`-typed tag with
`<Data Format="Message"><MessageParameters MessageType="CIP Generic" ServiceCode="16#004b"
ObjectType="16#0342" TargetObject="0" AttributeNumber="16#0000" ConnectedFlag=".." CommTypeCode="0"
ConnectionPath=".." RequestedLength=".." LocalIndex="0" DestinationTag=".."/></Data>`; executed with
`MSG(tag);` in ST.

**RESOLVED by live download+run on a 5069-L306ERS2 (2026-10-16) — the 4 rules that make CIP sockets
actually work from generated L5X:**
1. **`ConnectionPath="1, 0"`** (CompactLogix "message to self": port 1 backplane, slot 0 = the
   controller's own Ethernet) and **`ConnectedFlag="1"` = UNCONNECTED.** The L5X importer *rejects*
   `ConnectedFlag="0"` (import abort); connected-message samples use `"2"`. Front-Ethernet-port
   sockets MUST be unconnected (ENET-AT002) — verified live: `msg.UnconnectedTimeout` was populated
   (30 s), proving `"1"` is unconnected.
2. **MOV the created socket instance into every later MSG's writable `.Instance` member.** After
   CreateSocket `.DN`, do `msgOpen.Instance := crResult; msgWrite.Instance := crResult;
   msgRead.Instance := crResult; msgDelete.Instance := crResult;`. The create destination returns a
   *large* instance id (e.g. 30016), NOT 1-32, and it changes every create — hardcoding `TargetObject`
   fails OpenConnection with CIP status **5** (instance doesn't exist), and the wrong-instance
   DeleteSocket leaks sockets until CreateSocket hits CIP status **2** (resource exhausted).
3. **Set the writable `.REQ_LEN` member before every WriteSocket:** `msgWrite.REQ_LEN := 16 +
   writeP.BufLen;` (= Timeout 4 + ToAddr 8 + BufLen 4 + data). A fixed `RequestedLength` that exceeds
   `16 + BufLen` makes WriteSocket fail with CIP status **16#0015 "too much data."** (Per ENET-AT002:
   `WriteMSG.REQ_LEN := 4 + 12 + BufLen;`.) Create/Open/Read/Delete keep static RequestedLength
   (12 / 8+addrchars / 8 / 0).
4. `l5xgen.message_params_l5x` emits the correct defaults (`ConnectedFlag="1"`, `ConnectionPath="1, 0"`);
   `.Instance`/`.REQ_LEN` are set at runtime in the program ST, not in the tag config.

**Live-read tags on a controller whose online-correlate fails** (offline edits, or an offline child
module -> `RxCL_E_CANNOT_UPLOAD_PHYS_ADDR`): use `read_logix_telemetry(ip, ["Program:<prog>.<tag>"])`
(pycomm3) instead of LDSDK `go_online`. MESSAGE sub-members read fine: `.ERR`, `.EXERR`, `.DN`, `.ER`,
`.Instance`, `.UnconnectedTimeout`.

## Harvesting more structure
Real L5X templates on the VM: `C:\Users\Public\Documents\Studio 5000\Samples\*.L5X` (e.g.
`Add_On_Instructions_Samples.L5X` has AOIs + ST + a StringFamily type). Export any project to L5X via
the SDK (`save_as(..., detailed_l5x=True)`) to see exact current-version structure.
