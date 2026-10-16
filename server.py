"""rockwell_local - MCP server for Rockwell / ControlLogix engineering via the Logix Designer SDK.

Phase P1 (core): project lifecycle, build, L5X import/export, L5X parse/enumerate, and SDK
tag read/write. Online session + download + telemetry/any-protocol come in later phases.

Runs on the Windows VM next to Studio 5000 (Logix Designer v38) and LdSdkService. Launched by
run-mcp.cmd via `prlctl exec "Windows 11" --current-user ...\run-mcp.cmd`, mirroring codesys_local.
Python 3.12+.

Handle model: open_project / create_project load a LogixProject and cache it by absolute path;
every other tool acts on that cached handle via its `project_file_path`. close_project releases it.
The SDK cannot enumerate tags/programs, so parse_l5x reads an L5X export directly (no SDK needed).
"""
from __future__ import annotations

import asyncio
import os
import sys
import contextlib
import xml.etree.ElementTree as ET
from typing import Any

# --- .NET runtime roots -----------------------------------------------------------
# x64 for 64-bit Python/pythonnet; x86 for the SDK's 32-bit helper (FtspAdapterLDSDK.exe).
# These mirror the machine-wide env vars; set here too so the server works regardless of launcher.
os.environ.setdefault("DOTNET_ROOT", r"C:\Program Files\dotnet\x64")
os.environ["DOTNET_ROOT(x86)"] = r"C:\Program Files (x86)\dotnet"

# Import the SDK with stdout redirected to stderr so nothing can corrupt the MCP stdio stream.
with contextlib.redirect_stdout(sys.stderr):
    from logix_designer_sdk.logix_project import LogixProject
    from logix_designer_sdk import enums

from mcp.server.mcpserver import MCPServer

import l5xgen  # pure-Python L5X code generation (no SDK dependency)
import telemetry  # any-protocol telemetry: simulate / publish / read (no SDK dependency)
import fatsat  # FAT/SAT assertion ops + report formatting (no SDK dependency)

PHASE = "P5"
VERSION = "0.5.0"

server = MCPServer(
    name="rockwell_local",
    version=VERSION,
    instructions=(
        "Rockwell/ControlLogix engineering via the Logix Designer SDK on the Windows VM. "
        "Open or create a project first (open_project / create_project); other tools act on the "
        "open handle identified by its project_file_path. Use parse_l5x to enumerate a project's "
        "contents (the SDK cannot list tags/programs). Online session, download to hardware, and "
        "telemetry are not part of this phase."
    ),
)

# --- open-project handle registry -------------------------------------------------
_OPEN: dict[str, "LogixProject"] = {}


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _require(path: str) -> "LogixProject":
    k = _key(path)
    proj = _OPEN.get(k)
    if proj is None:
        raise KeyError(
            f"Project not open: {path}. Call open_project or create_project first."
        )
    return proj


def _ok(**kw: Any) -> dict:
    out = {"ok": True}
    out.update(kw)
    return out


async def _await(x):
    """Await x if it is a coroutine, else return it. SDK methods are coroutines; a few
    static helpers may not be, so this keeps call sites uniform."""
    return await x if asyncio.iscoroutine(x) else x


# Tag data type -> get/set method suffix on LogixProject.
_SUFFIX = {
    "BOOL": "bool", "SINT": "sint", "INT": "int", "DINT": "dint", "LINT": "lint",
    "USINT": "usint", "UINT": "uint", "UDINT": "udint", "ULINT": "ulint",
    "REAL": "real", "LREAL": "lreal", "STRING": "string",
}
_INT_TYPES = {"SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT"}


def _op_mode(mode: str):
    return enums.OperationMode[mode.strip().upper()]


def _tag_xpath(tag: str, program: str = None) -> str:
    """The SDK addresses tags by an XPath, not a bare name (a bare name raises XMLSrv_E_INVALID_TOKEN).
    Accept a plain name (-> controller scope, or program scope if `program` given) or pass through an
    XPath the caller already built."""
    if "[" in tag or tag.startswith("Controller/"):
        return tag
    if program:
        return f"Controller/Programs/Program[@Name='{program}']/Tags/Tag[@Name='{tag}']"
    return f"Controller/Tags/Tag[@Name='{tag}']"


def _coerce(value: str, data_type: str):
    dt = data_type.strip().upper()
    if dt == "BOOL":
        return str(value).strip().lower() in ("1", "true", "on", "yes", "t")
    if dt in _INT_TYPES:
        return int(value)
    if dt in ("REAL", "LREAL"):
        return float(value)
    return str(value)


# =====================================================================================
# Diagnostics
# =====================================================================================
async def server_info() -> dict:
    """Report server version/phase, which projects are currently open, and the .NET roots.
    Use this to confirm the SDK bridge is wired up before doing project work."""
    return _ok(
        name="rockwell_local",
        version=VERSION,
        phase=PHASE,
        open_projects=list(_OPEN.keys()),
        dotnet_root=os.environ.get("DOTNET_ROOT"),
        dotnet_root_x86=os.environ.get("DOTNET_ROOT(x86)"),
    )


async def get_processor_types(major_revision: int) -> dict:
    """List the controller catalog numbers the installed Logix Designer supports at a given
    major revision (e.g. 38). Does not need an open project."""
    res = list(await _await(LogixProject.get_processor_types(major_revision)))
    return _ok(major_revision=major_revision, count=len(res), processor_types=res)


# =====================================================================================
# Project lifecycle
# =====================================================================================
async def create_project(
    project_file_path: str,
    major_revision: int,
    processor_type: str,
    controller_name: str,
) -> dict:
    """Create a new .ACD project at the given path and leave it open (cached by path).
    processor_type is a catalog number from get_processor_types (e.g. 1756-L85E)."""
    proj = await _await(
        LogixProject.create_new_project(
            project_file_path, major_revision, processor_type, controller_name
        )
    )
    _OPEN[_key(project_file_path)] = proj
    return _ok(
        project_file_path=project_file_path,
        controller_name=controller_name,
        processor_type=processor_type,
        major_revision=major_revision,
    )


async def open_project(project_file_path: str) -> dict:
    """Open an existing .ACD project and cache the handle by path. Opening a project from an
    older Logix revision may take a while as Logix Designer converts it."""
    proj = await _await(LogixProject.open_logix_project(project_file_path))
    _OPEN[_key(project_file_path)] = proj
    return _ok(project_file_path=project_file_path, opened=True)


async def convert_project(project_file_path: str, destination_revision: int) -> dict:
    """Convert/upgrade a project to a newer Logix major revision and leave it open (cached by
    the same path)."""
    proj = await _await(
        LogixProject.convert(project_file_path, destination_revision)
    )
    _OPEN[_key(project_file_path)] = proj
    return _ok(project_file_path=project_file_path, destination_revision=destination_revision)


async def save_project(project_file_path: str) -> dict:
    """Save the open project to its .ACD file."""
    await _await(_require(project_file_path).save())
    return _ok(project_file_path=project_file_path, saved=True)


async def save_project_as(
    project_file_path: str,
    save_path: str,
    force: bool = False,
    detailed_l5x: bool = False,
) -> dict:
    """Save the open project to a new path. If save_path ends in .L5X, this is an export; set
    detailed_l5x=True for a full-content L5X. force overwrites an existing file."""
    await _await(_require(project_file_path).save_as(save_path, force, detailed_l5x))
    return _ok(project_file_path=project_file_path, save_path=save_path, detailed_l5x=detailed_l5x)


async def export_l5x(project_file_path: str, l5x_file_path: str) -> dict:
    """Convenience: export the open project to a full-content .L5X (save_as with force and
    detailed_l5x). Use parse_l5x on the result to enumerate contents."""
    await _await(_require(project_file_path).save_as(l5x_file_path, True, True))
    size = os.path.getsize(l5x_file_path) if os.path.exists(l5x_file_path) else None
    return _ok(project_file_path=project_file_path, l5x_file_path=l5x_file_path, bytes=size)


async def close_project(project_file_path: str) -> dict:
    """Close the open project and release its handle."""
    k = _key(project_file_path)
    proj = _OPEN.pop(k, None)
    if proj is None:
        return _ok(project_file_path=project_file_path, closed=False, note="was not open")
    await _await(proj.close())
    return _ok(project_file_path=project_file_path, closed=True)


async def list_open_projects() -> dict:
    """List the project paths currently open/cached in this server."""
    return _ok(open_projects=list(_OPEN.keys()), count=len(_OPEN))


async def change_controller_type(project_file_path: str, processor_type: str) -> dict:
    """Change the controller catalog number of the open project (e.g. migrate 1756-L71 ->
    1756-L85E). processor_type must be valid for the project's revision."""
    await _await(_require(project_file_path).change_controller_type(processor_type))
    return _ok(project_file_path=project_file_path, processor_type=processor_type)


# =====================================================================================
# Build
# =====================================================================================
async def build_project(project_file_path: str, target: str = "DEFAULT_TARGET") -> dict:
    """Compile the open project. target is one of DEFAULT_TARGET, ECHO_CONTROLLER,
    PHYSICAL_CONTROLLER. Raises with the SDK message if the build fails."""
    t = enums.RequestedBuildTarget[target.strip().upper()]
    await _await(_require(project_file_path).build(t))
    return _ok(project_file_path=project_file_path, target=target, built=True)


# =====================================================================================
# L5X import / export (partial)
# =====================================================================================
async def partial_export_xml(project_file_path: str, element_path: str, output_file: str) -> dict:
    """Partial-export a single element (by its x-path, e.g. Controller/Programs/MainProgram) of
    the open project to an XML/L5X file."""
    await _await(_require(project_file_path).partial_export_to_xml_file(element_path, output_file))
    size = os.path.getsize(output_file) if os.path.exists(output_file) else None
    return _ok(project_file_path=project_file_path, element_path=element_path, output_file=output_file, bytes=size)


async def partial_import_xml(
    project_file_path: str,
    element_path: str,
    xml_file: str,
    collision_option: str = "OVERWRITE_ON_COLL",
    continue_on_errors: bool = False,
) -> dict:
    """Partial-import an XML/L5X file into the open project at the given x-path. collision_option
    is CANCEL_ON_COLL, DISCARD_ON_COLL, or OVERWRITE_ON_COLL."""
    opt = enums.ImportCollisionOptions[collision_option.strip().upper()]
    await _await(
        _require(project_file_path).partial_import_from_xml_file(
            element_path, xml_file, opt, continue_on_errors
        )
    )
    return _ok(project_file_path=project_file_path, element_path=element_path, xml_file=xml_file, collision_option=collision_option)


async def partial_import_rungs(
    project_file_path: str,
    element_path: str,
    insert_position: int,
    replace_count: int,
    xml_file: str,
    online_import_option: str = "LEAVE_EDITS",
) -> dict:
    """Partial-import ladder rungs from an XML/L5X file into a routine (element_path). Inserts at
    insert_position, replacing replace_count rungs. online_import_option is ACCEPT_EDITS,
    FINALIZE_EDITS, or LEAVE_EDITS."""
    opt = enums.PartialImportOption[online_import_option.strip().upper()]
    await _await(
        _require(project_file_path).partial_import_rungs_from_xml_file(
            element_path, insert_position, replace_count, xml_file, opt
        )
    )
    return _ok(project_file_path=project_file_path, element_path=element_path, insert_position=insert_position, replace_count=replace_count)


# =====================================================================================
# Tag read / write
# =====================================================================================
async def read_tag(project_file_path: str, tag_path: str, data_type: str, mode: str = "OFFLINE",
                   program: str = None) -> dict:
    """Read a tag value from the open project. tag_path may be a bare tag name (resolved to the
    controller scope, or program scope if `program` is given) or a full XPath. data_type is one of
    BOOL, SINT, INT, DINT, LINT, USINT, UINT, UDINT, ULINT, REAL, LREAL, STRING. mode is OFFLINE
    (project value) or ONLINE (live, requires an online session)."""
    dt = data_type.strip().upper()
    suffix = _SUFFIX.get(dt)
    if suffix is None:
        raise ValueError(f"Unsupported data_type: {data_type}. Supported: {sorted(_SUFFIX)}")
    proj = _require(project_file_path)
    getter = getattr(proj, f"get_tag_value_{suffix}")
    value = await _await(getter(_tag_xpath(tag_path, program), _op_mode(mode)))
    return _ok(tag_path=tag_path, data_type=dt, mode=mode.upper(), value=value)


async def write_tag(project_file_path: str, tag_path: str, value: str, data_type: str, mode: str = "OFFLINE",
                    program: str = None) -> dict:
    """Write a tag value in the open project. tag_path may be a bare tag name (controller scope, or
    program scope if `program` is given) or a full XPath. value is a string coerced to data_type
    (BOOL accepts 1/true/on/yes). mode is OFFLINE (project value, then save_project to persist) or
    ONLINE (live write - consequential). Save the project to persist OFFLINE writes."""
    dt = data_type.strip().upper()
    suffix = _SUFFIX.get(dt)
    if suffix is None:
        raise ValueError(f"Unsupported data_type: {data_type}. Supported: {sorted(_SUFFIX)}")
    proj = _require(project_file_path)
    setter = getattr(proj, f"set_tag_value_{suffix}")
    coerced = _coerce(value, dt)
    await _await(setter(_tag_xpath(tag_path, program), _op_mode(mode), coerced))
    return _ok(tag_path=tag_path, data_type=dt, mode=mode.upper(), value=coerced, note="call save_project to persist an OFFLINE write")


# =====================================================================================
# L5X parse / enumerate (pure XML - the SDK cannot enumerate)
# =====================================================================================
def _tags_under(parent) -> list[dict]:
    names: list[dict] = []
    tags_el = parent.find("Tags")
    if tags_el is not None:
        for t in tags_el.findall("Tag"):
            names.append({"name": t.get("Name"), "data_type": t.get("DataType")})
    return names


async def parse_l5x(l5x_file_path: str) -> dict:
    """Parse an L5X (full or partial) export and enumerate its contents without the SDK:
    controller, programs + routines, controller-scope and program-scope tags, Add-On
    Instructions, UDTs, and modules. Pair with export_l5x to inspect any open project."""
    root = ET.parse(l5x_file_path).getroot()
    out: dict[str, Any] = {"ok": True, "file": l5x_file_path}

    ctrl = root.find(".//Controller")
    if ctrl is not None:
        out["controller"] = {
            "name": ctrl.get("Name"),
            "processor_type": ctrl.get("ProcessorType"),
            "major_rev": ctrl.get("MajorRev"),
            "minor_rev": ctrl.get("MinorRev"),
        }

    programs = []
    program_tags: dict[str, list] = {}
    for p in root.iter("Program"):
        routines = [r.get("Name") for r in p.iter("Routine")]
        programs.append({"name": p.get("Name"), "routines": routines, "routine_count": len(routines)})
        program_tags[p.get("Name")] = _tags_under(p)

    controller_tags = _tags_under(ctrl) if ctrl is not None else []
    aois = [a.get("Name") for a in root.iter("AddOnInstructionDefinition")]
    udts = [d.get("Name") for d in root.iter("DataType")]
    modules = [{"name": m.get("Name"), "catalog": m.get("CatalogNumber")} for m in root.iter("Module")]

    out["programs"] = programs
    out["controller_tags"] = controller_tags
    out["program_tags"] = program_tags
    out["aois"] = aois
    out["udts"] = udts
    out["modules"] = modules
    out["counts"] = {
        "programs": len(programs),
        "controller_tags": len(controller_tags),
        "program_tags": sum(len(v) for v in program_tags.values()),
        "aois": len(aois),
        "udts": len(udts),
        "modules": len(modules),
    }
    return out


# =====================================================================================
# Codegen (P2) - generate L5X from intent, validate by compile, diff
# =====================================================================================
async def generate_project_from_points(
    output_l5x_path: str,
    controller_name: str,
    points: list,
    processor_type: str = "1756-L85E",
    major_revision: int = 38,
    program_name: str = "MainProgram",
    main_routine: str = "MainRoutine",
    rungs: list = None,
    udts: list = None,
) -> dict:
    """Generate a full, compilable L5X project from a points list, scaffolding a scheduled program
    (so the project is not bare). points: [{name, type, description?}] where type is a Logix atomic
    type (BOOL for DI/DO, REAL for AI/AO, etc.). udts: [{name, members:[{name, data_type}]}]. rungs:
    optional ladder-text rungs for the main routine. Writes output_l5x_path; validate_l5x to compile.
    Clone-and-splice: a bare SDK project is the template, generated elements are spliced in."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="rk_gen_")
    base_acd = os.path.join(tmp, "base.ACD")
    base_l5x = os.path.join(tmp, "base.L5X")
    bp = await _await(LogixProject.create_new_project(base_acd, major_revision, processor_type, controller_name))
    await _await(bp.save_as(base_l5x, True, True))
    await _await(bp.close())
    base = open(base_l5x, encoding="utf-8").read()
    prog = {"name": program_name, "main_routine": main_routine,
            "routines": [{"name": main_routine, "rungs": rungs or []}]}
    gen = l5xgen.inject(base, tags=l5xgen.tags_from_points(points), udts=udts or [],
                        programs=[prog], schedule=[program_name])
    with open(output_l5x_path, "w", encoding="utf-8") as f:
        f.write(gen)
    return _ok(output_l5x_path=output_l5x_path, controller_name=controller_name,
               processor_type=processor_type, major_revision=major_revision,
               tag_count=len(points), program=program_name, udt_count=len(udts or []),
               note="run validate_l5x to compile it, or open_project to build/download")


async def validate_l5x(l5x_file_path: str, build: bool = True) -> dict:
    """Open an L5X as a project and (optionally) compile it to prove it is valid Logix, then
    enumerate what it contains. The strongest check that generated L5X is correct."""
    import tempfile
    proj = await _await(LogixProject.open_logix_project(l5x_file_path))
    tmp = tempfile.mkdtemp(prefix="rk_val_")
    acd = os.path.join(tmp, "validate.ACD")
    out_l5x = os.path.join(tmp, "validate.L5X")
    build_ok = None
    build_error = None
    try:
        await _await(proj.save_as(acd, True, False))
        if build:
            try:
                await _await(proj.build())
                build_ok = True
            except Exception as e:
                build_ok = False
                build_error = f"{type(e).__name__}: {e}"
        await _await(proj.save_as(out_l5x, True, True))
    finally:
        await _await(proj.close())
    info = await parse_l5x(out_l5x)
    return _ok(l5x_file_path=l5x_file_path, build=build, build_ok=build_ok,
               build_error=build_error, controller=info.get("controller"),
               counts=info.get("counts"), programs=info.get("programs"))


async def diff_l5x(l5x_a: str, l5x_b: str) -> dict:
    """Structural diff between two L5X files: which programs, controller tags, AOIs and UDTs were
    added or removed going from A to B. Useful for reviewing generated or edited code."""
    a = await parse_l5x(l5x_a)
    b = await parse_l5x(l5x_b)

    def progs(d):
        return {p["name"] for p in d.get("programs", [])}

    def ctags(d):
        return {t["name"] for t in d.get("controller_tags", [])}

    def setof(d, key):
        return set(d.get(key, []))

    def delta(sa, sb):
        return {"added": sorted(sb - sa), "removed": sorted(sa - sb)}

    return _ok(
        a=l5x_a, b=l5x_b,
        programs=delta(progs(a), progs(b)),
        controller_tags=delta(ctags(a), ctags(b)),
        aois=delta(setof(a, "aois"), setof(b, "aois")),
        udts=delta(setof(a, "udts"), setof(b, "udts")),
    )


# =====================================================================================
# Comms & online (P3) - acts on a live controller; mutating ops are confirm-gated
# =====================================================================================
# These operate on a physical (or emulated) controller over a communications path. Anything that
# changes the controller or the project is gated: without confirm=true it returns a dry-run plan and
# does NOT execute. This is a safety guardrail - downloading or changing mode on a running
# controller is consequential and must be an explicit, deliberate act. A human confirms each time.
def _dry_run(action: str, **details: Any) -> dict:
    return {"ok": True, "dry_run": True, "would": action, "details": details,
            "note": "Acts on a live controller. Re-call with confirm=true to actually execute."}


def build_comm_path_str(driver: str, address: str, backplane_slot: int = 0) -> str:
    """Format a CIP communications-path string (pure; touches no hardware)."""
    return "\\".join([driver, address, "Backplane", str(backplane_slot)])


async def build_comm_path(driver: str, address: str, backplane_slot: int = 0) -> dict:
    """Format a CIP comm-path string for set_comm_path. driver is a FactoryTalk Linx driver name
    (e.g. AB_ETHIP-1 for EtherNet/IP); address is the controller IP/host; backplane_slot is the
    CPU slot. Does not contact the controller."""
    return _ok(comm_path=build_comm_path_str(driver, address, backplane_slot))


async def set_comm_path(project_file_path: str, comm_path: str) -> dict:
    """Set the open project's communications path (project metadata; does not connect)."""
    await _await(_require(project_file_path).set_communications_path(comm_path))
    return _ok(project_file_path=project_file_path, comm_path=comm_path)


async def get_comm_path(project_file_path: str) -> dict:
    """Read the open project's configured communications path."""
    cp = await _await(_require(project_file_path).get_communications_path())
    return _ok(project_file_path=project_file_path, comm_path=cp)


async def read_connected_state(project_file_path: str) -> dict:
    """Read whether the project is connected/online (read-only)."""
    st = await _await(_require(project_file_path).read_connected_state())
    return _ok(project_file_path=project_file_path, connected_state=str(st))


async def read_controller_mode(project_file_path: str) -> dict:
    """Read the controller's key mode (PROGRAM/RUN/TEST/FAULTED) - requires an online session."""
    m = await _await(_require(project_file_path).read_controller_mode())
    return _ok(project_file_path=project_file_path, controller_mode=str(m))


async def go_online(project_file_path: str, confirm: bool = False) -> dict:
    """Go online with the controller at the project's comm path. Confirm-gated (establishes a live
    session). Set the comm path first with set_comm_path."""
    if not confirm:
        return _dry_run("go_online", project_file_path=project_file_path)
    await _await(_require(project_file_path).go_online())
    return _ok(project_file_path=project_file_path, online=True)


async def go_offline(project_file_path: str) -> dict:
    """Drop the online session (safe)."""
    await _await(_require(project_file_path).go_offline())
    return _ok(project_file_path=project_file_path, online=False)


async def change_controller_mode(project_file_path: str, mode: str, confirm: bool = False) -> dict:
    """Change the controller key mode: PROGRAM, RUN, or TEST. Confirm-gated - putting a controller
    in RUN actuates real equipment."""
    m = enums.RequestedControllerMode[mode.strip().upper()]
    if not confirm:
        return _dry_run("change_controller_mode", project_file_path=project_file_path, mode=mode.upper(),
                        warning="RUN energizes outputs / actuates equipment.")
    await _await(_require(project_file_path).change_controller_mode(m))
    return _ok(project_file_path=project_file_path, mode=mode.upper())


async def download(project_file_path: str, confirm: bool = False) -> dict:
    """Download the open project to the controller. Confirm-gated - OVERWRITES the controller's
    running program and faults it to PROGRAM during the download."""
    if not confirm:
        return _dry_run("download", project_file_path=project_file_path,
                        warning="Overwrites the controller's program; stops the running process.")
    await _await(_require(project_file_path).download())
    return _ok(project_file_path=project_file_path, downloaded=True)


async def upload(project_file_path: str, confirm: bool = False) -> dict:
    """Upload the controller's contents into the open project. Confirm-gated - overwrites the open
    project in memory with what is on the controller."""
    if not confirm:
        return _dry_run("upload", project_file_path=project_file_path,
                        warning="Overwrites the open project with the controller's contents.")
    await _await(_require(project_file_path).upload())
    return _ok(project_file_path=project_file_path, uploaded=True)


async def upload_to_new_project(project_file_path: str, comm_path: str, confirm: bool = False) -> dict:
    """Create a new project file from the controller at comm_path and open it. Confirm-gated
    (contacts the controller)."""
    if not confirm:
        return _dry_run("upload_to_new_project", project_file_path=project_file_path, comm_path=comm_path)
    proj = await _await(LogixProject.upload_to_new_project(project_file_path, comm_path))
    _OPEN[_key(project_file_path)] = proj
    return _ok(project_file_path=project_file_path, comm_path=comm_path, created=True)


# =====================================================================================
# Telemetry (P4) - any-protocol data layer, publish, simulate (no hardware needed to simulate)
# =====================================================================================
async def simulate_telemetry(points: list, samples: int = 10, interval_ms: int = 1000,
                             output_jsonl: str = None) -> dict:
    """Generate synthetic telemetry for a set of points (no hardware). Each point:
    {name, kind: analog|bool, behavior: sine|ramp|walk|toggle|constant|random, lo, hi, period_s,
    unit, description}. Returns a preview and, if output_jsonl is given, writes all records there."""
    data = telemetry.simulate(points, samples, interval_ms / 1000.0)
    path = telemetry.to_jsonl(data, output_jsonl) if output_jsonl else None
    return _ok(points=len(points), samples=samples, total_records=len(data),
               preview=data[: min(6, len(data))], jsonl_path=path)


async def telemetry_selftest(points: list = None, samples: int = 5) -> dict:
    """Prove the simulate -> publish -> read pipeline with no hardware: spin up an in-process OPC UA
    server, publish simulated point values, and read them back with a client."""
    if not points:
        points = [{"name": "CHWS_Temp", "kind": "analog", "behavior": "sine", "lo": 40, "hi": 55, "unit": "degF"},
                  {"name": "CDU_Flow", "kind": "analog", "behavior": "walk", "lo": 0, "hi": 300, "unit": "gpm"},
                  {"name": "Pump1_Run", "kind": "bool", "behavior": "toggle", "period_s": 2}]
    read_back = await telemetry.opcua_roundtrip(points, samples)
    return _ok(opcua_roundtrip_ok=True, points=len(points), read_back=read_back,
               note="in-process OPC UA server+client; no hardware involved")


async def publish_telemetry_mqtt(points: list, samples: int = 10, host: str = "127.0.0.1",
                                 port: int = 1883, topic_prefix: str = "bms/telemetry",
                                 interval_ms: int = 0) -> dict:
    """Publish simulated telemetry to an MQTT broker (one JSON message per sample under
    <topic_prefix>/<name>). Requires a reachable broker; returns an error if none is available."""
    data = telemetry.simulate(points, samples, interval_ms / 1000.0)
    try:
        n = telemetry.mqtt_publish(data, host, port, topic_prefix)
        return _ok(published=n, host=host, port=port, topic_prefix=topic_prefix)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "note": "needs a reachable MQTT broker at host:port"}


async def read_logix_telemetry(plc_ip: str, tag_names: list) -> dict:
    """Read live tag values from a Logix controller over EtherNet/IP (pycomm3). Requires a reachable
    controller; returns an error otherwise. Values come back in the same shape as simulated points."""
    try:
        values = telemetry.read_logix_tags(plc_ip, tag_names)
        return _ok(plc_ip=plc_ip, values=values)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "note": "needs a reachable ControlLogix/CompactLogix controller at plc_ip"}


# =====================================================================================
# FAT/SAT test runner (P5) - assert expected tag values/states, emit a report
# =====================================================================================
async def run_fat_suite(project_file_path: str, suite: dict, mode: str = "OFFLINE") -> dict:
    """Run a FAT/SAT suite against an open project's tags. Each case optionally sets tags (setup),
    then asserts (checks). mode OFFLINE reads/writes the project's values (no hardware); mode ONLINE
    requires a live session. See fatsat.py for the suite shape. Returns per-check results + a report."""
    proj = _require(project_file_path)
    om = _op_mode(mode)
    cases_out = []
    for case in suite.get("cases", []):
        for s in case.get("setup", []):
            suffix = _SUFFIX[s["type"].upper()]
            await _await(getattr(proj, f"set_tag_value_{suffix}")(
                _tag_xpath(s["tag"], s.get("program")), om, _coerce(str(s["value"]), s["type"])))
        checks_out = []
        for chk in case.get("checks", []):
            suffix = _SUFFIX[chk["type"].upper()]
            actual = await _await(getattr(proj, f"get_tag_value_{suffix}")(
                _tag_xpath(chk["tag"], chk.get("program")), om))
            passed = fatsat.evaluate(chk["op"], actual, chk.get("value"))
            checks_out.append({"tag": chk["tag"], "op": chk["op"], "value": chk.get("value"),
                               "actual": actual, "passed": passed})
        cases_out.append({"name": case.get("name", "case"),
                          "passed": all(c["passed"] for c in checks_out) if checks_out else True,
                          "checks": checks_out})
    passed = sum(1 for c in cases_out if c["passed"])
    return _ok(suite=suite.get("name", "FAT/SAT"), mode=mode.upper(),
               cases_passed=passed, cases_total=len(cases_out), cases=cases_out,
               report_markdown=fatsat.report_markdown(suite.get("name", "FAT/SAT"), cases_out))


async def run_fat_suite_telemetry(points: list, suite: dict, samples: int = 30) -> dict:
    """Run a FAT/SAT suite against SIMULATED telemetry (no hardware). Checks reference a point and a
    stat (min|max|last) over the simulated window: {point, stat, op, value}. Good for proving alarm
    ranges and behavior before a controller exists."""
    data = telemetry.simulate(points, samples, 1.0)
    agg: dict = {}
    for s in data:
        a = agg.setdefault(s["name"], {"min": s["value"], "max": s["value"], "last": s["value"]})
        a["min"] = min(a["min"], s["value"])
        a["max"] = max(a["max"], s["value"])
        a["last"] = s["value"]
    cases_out = []
    for case in suite.get("cases", []):
        checks_out = []
        for chk in case.get("checks", []):
            stat = chk.get("stat", "last")
            actual = agg.get(chk["point"], {}).get(stat)
            passed = fatsat.evaluate(chk["op"], actual, chk.get("value"))
            checks_out.append({"tag": f"{chk['point']}.{stat}", "op": chk["op"],
                               "value": chk.get("value"), "actual": actual, "passed": passed})
        cases_out.append({"name": case.get("name", "case"),
                          "passed": all(c["passed"] for c in checks_out) if checks_out else True,
                          "checks": checks_out})
    passed = sum(1 for c in cases_out if c["passed"])
    return _ok(suite=suite.get("name", "Telemetry FAT"),
               cases_passed=passed, cases_total=len(cases_out), cases=cases_out,
               report_markdown=fatsat.report_markdown(suite.get("name", "Telemetry FAT"), cases_out))


async def controller_health(project_file_path: str) -> dict:
    """Report controller connection state (works offline) and, if online, the key mode. A quick
    pre/post-download health read for SAT."""
    proj = _require(project_file_path)
    cs = await _await(proj.read_connected_state())
    out = {"ok": True, "project_file_path": project_file_path, "connected_state": str(cs)}
    try:
        out["controller_mode"] = str(await _await(proj.read_controller_mode()))
    except Exception as e:
        out["controller_mode"] = None
        out["controller_mode_note"] = f"requires online session: {type(e).__name__}"
    return out


# =====================================================================================
# Registration + entrypoint
# =====================================================================================
_TOOLS = (
    server_info,
    get_processor_types,
    generate_project_from_points,
    validate_l5x,
    diff_l5x,
    simulate_telemetry,
    telemetry_selftest,
    publish_telemetry_mqtt,
    read_logix_telemetry,
    run_fat_suite,
    run_fat_suite_telemetry,
    controller_health,
    build_comm_path,
    set_comm_path,
    get_comm_path,
    read_connected_state,
    read_controller_mode,
    go_online,
    go_offline,
    change_controller_mode,
    download,
    upload,
    upload_to_new_project,
    create_project,
    open_project,
    convert_project,
    save_project,
    save_project_as,
    export_l5x,
    close_project,
    list_open_projects,
    change_controller_type,
    build_project,
    partial_export_xml,
    partial_import_xml,
    partial_import_rungs,
    read_tag,
    write_tag,
    parse_l5x,
)

for _fn in _TOOLS:
    server.tool()(_fn)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
