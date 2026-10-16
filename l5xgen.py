"""Pure-Python L5X code generation for Rockwell ControlLogix projects (no SDK required).

Strategy: clone a bare, SDK-exported project L5X as a template and splice generated
``<DataType>``, ``<Tag>`` and ``<Program>`` elements into the right containers, scheduling
programs under a task. The result is a full L5X project the Logix Designer SDK can open and build.

Why clone-and-splice: a valid Logix project needs a lot of controller/module boilerplate that is
painful and brittle to author from scratch. A bare project created by the SDK already has all of
it, so we only generate the parts that carry engineering intent (UDTs, tags, programs/routines).
Generation is pure XML, so it runs anywhere; the SDK is only needed to open/build/validate.

Element shapes are grounded in a real v38 L5X export (see rockwell-mcp-scope.md / P2 harvest).
"""
from __future__ import annotations

import re

ATOMIC = {"BOOL", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT", "REAL", "LREAL"}
_FLOAT = {"REAL", "LREAL"}


def _radix(dt: str) -> str:
    return "Float" if dt in _FLOAT else "Decimal"


def _default_value(dt: str) -> str:
    return "0.0" if dt in _FLOAT else "0"


# --- element builders ---------------------------------------------------------------
def tag_l5x(name, data_type, value=None, description=None,
            external_access="Read/Write", constant=False, message=None) -> str:
    """One controller- or program-scope tag. Atomic types get an initial value; structured/UDT
    tags are declared and left to Logix to initialize. Pass message={...} for a MESSAGE tag
    (MSG config) - MESSAGE tags must be program/controller scope (never AOI locals)."""
    if message is not None:
        d = f"<Description><![CDATA[{description}]]></Description>\n" if description else ""
        return (f'<Tag Name="{name}" TagType="Base" DataType="MESSAGE">\n{d}'
                f'<Data Format="Message">{message_params_l5x(**message)}</Data>\n</Tag>')
    dt = data_type.upper()
    desc = f"<Description><![CDATA[{description}]]></Description>\n" if description else ""
    if dt in ATOMIC:
        radix = _radix(dt)
        val = _default_value(dt) if value is None else str(value)
        return (f'<Tag Name="{name}" TagType="Base" DataType="{dt}" Radix="{radix}" '
                f'Constant="{"true" if constant else "false"}" ExternalAccess="{external_access}">\n'
                f'{desc}'
                f'<Data Format="L5K"><![CDATA[{val}]]></Data>\n'
                f'<Data Format="Decorated"><DataValue DataType="{dt}" Radix="{radix}" Value="{val}"/></Data>\n'
                f'</Tag>')
    return (f'<Tag Name="{name}" TagType="Base" DataType="{data_type}" '
            f'ExternalAccess="{external_access}">\n{desc}</Tag>')


def member_l5x(name, data_type, dimension=0, radix=None,
               external_access="Read/Write", hidden=False) -> str:
    dt = data_type.upper() if data_type.upper() in ATOMIC else data_type
    rad = radix or (_radix(dt) if dt in ATOMIC else "NullType")
    return (f'<Member Name="{name}" DataType="{data_type}" Dimension="{dimension}" '
            f'Radix="{rad}" Hidden="{"true" if hidden else "false"}" ExternalAccess="{external_access}"/>')


def datatype_l5x(name, members, family="NoFamily") -> str:
    """A user-defined type (UDT). members is a list of dicts (name, data_type, ...) or raw strings."""
    mem = "\n".join(member_l5x(**m) if isinstance(m, dict) else m for m in members)
    return (f'<DataType Name="{name}" Family="{family}" Class="User">\n'
            f'<Members>\n{mem}\n</Members>\n</DataType>')


def rung_l5x(number, text, comment=None) -> str:
    c = f"<Comment><![CDATA[{comment}]]></Comment>\n" if comment else ""
    return f'<Rung Number="{number}" Type="N">\n{c}<Text><![CDATA[{text}]]></Text>\n</Rung>'


def routine_l5x(name, rungs=None, type="RLL") -> str:
    rungs = rungs or []
    body = "\n".join(rung_l5x(i, r) if isinstance(r, str) else rung_l5x(number=i, **r)
                     for i, r in enumerate(rungs))
    return f'<Routine Name="{name}" Type="{type}">\n<RLLContent>\n{body}\n</RLLContent>\n</Routine>'


def program_l5x(name, main_routine, routines=None, scoped_tags=None) -> str:
    routines = routines or [{"name": main_routine, "rungs": []}]
    tags_xml = ""
    if scoped_tags:
        tags_xml = "<Tags>\n" + "\n".join(tag_l5x(**t) for t in scoped_tags) + "\n</Tags>\n"
    routines_xml = "<Routines>\n" + "\n".join(_render_routine(r) for r in routines) + "\n</Routines>"
    return (f'<Program Name="{name}" TestEdits="false" MainRoutineName="{main_routine}" '
            f'Disabled="false" Class="Standard" UseAsFolder="false">\n{tags_xml}{routines_xml}\n</Program>')


# --- Structured Text + Add-On Instructions ------------------------------------------
def st_routine_l5x(name: str, lines=None, routine_type: str = "ST") -> str:
    """A Structured Text routine. lines is a list of ST source lines (one <Line> each)."""
    lines = lines or []
    body = "\n".join(f'<Line Number="{i}"><![CDATA[{ln}]]></Line>' for i, ln in enumerate(lines))
    return f'<Routine Name="{name}" Type="{routine_type}">\n<STContent>\n{body}\n</STContent>\n</Routine>'


def _render_routine(r: dict) -> str:
    """Render a routine dict as RLL (rungs) or ST (lines)."""
    if str(r.get("type", "RLL")).upper() == "ST":
        return st_routine_l5x(r["name"], r.get("lines", []))
    return routine_l5x(r["name"], r.get("rungs", []), r.get("type", "RLL"))


def _radix_attr(dt: str) -> str:
    u = dt.upper()
    if u in _FLOAT:
        return ' Radix="Float"'
    if u in ATOMIC:
        return ' Radix="Decimal"'
    return ""  # UDT / AOI type carries no Radix


def _decorated_default(dt: str, value=None) -> str:
    u = dt.upper()
    if u not in ATOMIC:
        return ""
    v = _default_value(u) if value is None else str(value)
    return (f'<DefaultData Format="Decorated"><DataValue DataType="{u}" '
            f'Radix="{_radix(u)}" Value="{v}"/></DefaultData>')


def aoi_param_l5x(name, data_type, usage="Input", required=False, visible=True,
                  dimension=0, description=None, default=None) -> str:
    dim = f' Dimensions="{dimension}"' if dimension else ""
    desc = f'<Description><![CDATA[{description}]]></Description>' if description else ""
    inner = desc + ("" if usage == "InOut" else _decorated_default(data_type, default))
    return (f'<Parameter Name="{name}" DataType="{data_type}" Usage="{usage}"'
            f'{_radix_attr(data_type)}{dim} Required="{"true" if required else "false"}" '
            f'Visible="{"true" if visible else "false"}">{inner}</Parameter>')


def message_params_l5x(service_code, obj_class="16#0342", instance="0", req_len="0",
                       local_element="", destination_tag="", connected_flag="1",
                       comm_type="0", conn_path="1, 0", attribute="16#0000") -> str:
    """A CIP Generic <MessageParameters> element (for a MESSAGE tag). Defaults target the socket
    object (class 0x342) via "Message to Self": ConnectionPath "1, 0" (the CompactLogix slot-0
    backplane path to the controller's own Ethernet, per ENET-AT002). ConnectedFlag "1" =
    unconnected (required for front-Ethernet-port sockets); the L5X importer REJECTS "0", and
    "2"/"3" are the connected variants. local_element = source tag; destination_tag = response
    tag."""
    le = f' LocalElement="{local_element}"' if local_element else ""
    dt = f' DestinationTag="{destination_tag}"' if destination_tag else ""
    return (f'<MessageParameters MessageType="CIP Generic" RequestedLength="{req_len}" '
            f'ConnectedFlag="{connected_flag}" ConnectionPath="{conn_path}" CommTypeCode="{comm_type}" '
            f'ServiceCode="{service_code}" ObjectType="{obj_class}" TargetObject="{instance}" '
            f'AttributeNumber="{attribute}" LocalIndex="0"{le}{dt}/>')


def aoi_localtag_l5x(name, data_type, dimension=0, description=None, default=None, message=None) -> str:
    dim = f' Dimensions="{dimension}"' if dimension else ""
    desc = f'<Description><![CDATA[{description}]]></Description>' if description else ""
    if message is not None:  # a MESSAGE-typed local tag with CIP config
        return (f'<LocalTag Name="{name}" DataType="MESSAGE">{desc}'
                f'<Data Format="Message">{message_params_l5x(**message)}</Data></LocalTag>')
    return (f'<LocalTag Name="{name}" DataType="{data_type}"{_radix_attr(data_type)}{dim}>'
            f'{desc}{_decorated_default(data_type, default)}</LocalTag>')


def aoi_l5x(name, parameters=None, local_tags=None, st_lines=None, revision="1.0",
            description=None) -> str:
    """An Add-On Instruction with an ST 'Logic' routine. EnableIn/EnableOut are added automatically.
    parameters/local_tags are kwargs for aoi_param_l5x / aoi_localtag_l5x; st_lines is the Logic ST.
    Put an InOut UDT parameter (e.g. the equipment data struct) for the AOI to read/write in place."""
    params = [aoi_param_l5x("EnableIn", "BOOL", "Input", visible=False),
              aoi_param_l5x("EnableOut", "BOOL", "Output", visible=False)]
    params += [aoi_param_l5x(**p) for p in (parameters or [])]
    lt = [aoi_localtag_l5x(**t) for t in (local_tags or [])]
    lt_block = ("<LocalTags>\n" + "\n".join(lt) + "\n</LocalTags>\n") if lt else ""
    desc = f'<Description><![CDATA[{description}]]></Description>\n' if description else ""
    logic = st_routine_l5x("Logic", st_lines or [])
    return (f'<AddOnInstructionDefinition Name="{name}" Revision="{revision}" '
            f'ExecutePrescan="false" ExecutePostscan="false" ExecuteEnableInFalse="false" '
            f'SoftwareRevision="v38.00">\n{desc}'
            f'<Parameters>\n' + "\n".join(params) + "\n</Parameters>\n"
            f'{lt_block}<Routines>\n{logic}\n</Routines>\n</AddOnInstructionDefinition>')


# --- splicing -----------------------------------------------------------------------
def _inject_before_close(xml: str, container: str, payload: str) -> str:
    """Insert payload inside <container>…</container>, expanding a self-closing <container/>."""
    self_close = re.compile(fr"<{container}\s*/>")
    if self_close.search(xml):
        return self_close.sub(f"<{container}>\n{payload}\n</{container}>", xml, count=1)
    return re.sub(fr"</{container}>", f"{payload}\n</{container}>", xml, count=1)


def _schedule(xml: str, task: str, programs: list[str]) -> str:
    """Schedule programs under `task`. A bare SDK project exports an EMPTY <Tasks/> with no task at
    all, so when the named task is absent we create a CONTINUOUS task — otherwise a program compiles
    but never runs. (Do not use `\\b` right after the closing quote in these patterns: after a quote
    it sits between two non-word chars and never matches.)"""
    sp = "\n".join(f'<ScheduledProgram Name="{n}"/>' for n in programs)

    def expand(block: str) -> str:
        if re.search(r"<ScheduledPrograms\s*/>", block):
            return re.sub(r"<ScheduledPrograms\s*/>",
                          f"<ScheduledPrograms>\n{sp}\n</ScheduledPrograms>", block, count=1)
        if "<ScheduledPrograms>" in block:
            return re.sub(r"</ScheduledPrograms>", f"{sp}\n</ScheduledPrograms>", block, count=1)
        return re.sub(r"</Task>", f"<ScheduledPrograms>\n{sp}\n</ScheduledPrograms>\n</Task>", block, count=1)

    # 1) existing <Task Name="task" ...> ... </Task>
    full = re.search(fr'<Task Name="{task}"[^>]*>.*?</Task>', xml, flags=re.S)
    if full:
        return xml[:full.start()] + expand(full.group(0)) + xml[full.end():]
    # 2) self-closing <Task Name="task" .../>
    sc = re.search(fr'<Task Name="{task}"[^>]*/>', xml)
    if sc:
        opened = sc.group(0)[:-2] + ">"  # drop '/>' add '>'
        return xml[:sc.start()] + opened + f"\n<ScheduledPrograms>\n{sp}\n</ScheduledPrograms>\n</Task>" + xml[sc.end():]
    # 3) no task at all (bare base has <Tasks/>): create a CONTINUOUS task
    new_task = (f'<Task Name="{task}" Type="CONTINUOUS" Priority="10" Watchdog="500" '
                f'DisableUpdateOutputs="false" InhibitTask="false" Class="Standard">\n'
                f'<ScheduledPrograms>\n{sp}\n</ScheduledPrograms>\n</Task>')
    return _inject_before_close(xml, "Tasks", new_task)


def inject(base_l5x: str, tags=None, udts=None, aois=None, programs=None, schedule=None, task="MainTask") -> str:
    """Splice generated elements into a cloned bare-project L5X and return the full project L5X.

    tags/udts: lists of kwargs for tag_l5x/datatype_l5x (controller scope).
    aois: list of kwargs for aoi_l5x. programs: kwargs for program_l5x. schedule: program names to run.
    """
    xml = base_l5x
    if udts:
        xml = _inject_before_close(xml, "DataTypes", "\n".join(datatype_l5x(**u) for u in udts))
    if aois:
        block = "\n".join(aoi_l5x(**a) for a in aois)
        if "<AddOnInstructionDefinitions" in xml:
            xml = _inject_before_close(xml, "AddOnInstructionDefinitions", block)
        else:
            # bare base has no AOI container; it belongs after Modules, before controller Tags
            head, sep, tail = xml.partition("<Tags")
            xml = head + "<AddOnInstructionDefinitions>\n" + block + "\n</AddOnInstructionDefinitions>\n" + sep + tail
    if tags:
        # controller-scope Tags live before <Tasks ; operate on that region only
        head, sep, tail = xml.partition("<Tasks")
        head = _inject_before_close(head, "Tags", "\n".join(tag_l5x(**t) for t in tags))
        xml = head + sep + tail
    if programs:
        xml = _inject_before_close(xml, "Programs", "\n".join(program_l5x(**p) for p in programs))
    if schedule:
        xml = _schedule(xml, task, schedule)
    return xml


# --- points-list convenience --------------------------------------------------------
def tags_from_points(points: list[dict]) -> list[dict]:
    """Map a simple points list to tag kwargs. Each point: {name, type, description?}.
    `type` is a Logix atomic type (BOOL for DI/DO, REAL for AI/AO, etc.)."""
    out = []
    for p in points:
        out.append({"name": p["name"], "data_type": p.get("type", "REAL"),
                    "description": p.get("description")})
    return out
