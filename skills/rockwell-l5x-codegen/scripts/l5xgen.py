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
            external_access="Read/Write", constant=False) -> str:
    """One controller- or program-scope tag. Atomic types get an initial value; structured/UDT
    tags are declared and left to Logix to initialize."""
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
    routines_xml = "<Routines>\n" + "\n".join(routine_l5x(**r) for r in routines) + "\n</Routines>"
    return (f'<Program Name="{name}" TestEdits="false" MainRoutineName="{main_routine}" '
            f'Disabled="false" Class="Standard" UseAsFolder="false">\n{tags_xml}{routines_xml}\n</Program>')


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


def inject(base_l5x: str, tags=None, udts=None, programs=None, schedule=None, task="MainTask") -> str:
    """Splice generated elements into a cloned bare-project L5X and return the full project L5X.

    tags/udts: lists of kwargs for tag_l5x/datatype_l5x (controller scope).
    programs: list of kwargs for program_l5x. schedule: list of program names to run under `task`.
    """
    xml = base_l5x
    if udts:
        xml = _inject_before_close(xml, "DataTypes", "\n".join(datatype_l5x(**u) for u in udts))
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
