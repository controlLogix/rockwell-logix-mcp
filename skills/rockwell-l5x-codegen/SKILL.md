---
name: rockwell-l5x-codegen
description: >-
  Generate compilable Rockwell / ControlLogix code as L5X — tags from a points list, UDTs,
  programs, routines and ladder rungs — using the clone-and-splice method, and validate it by
  compiling. Use this whenever the user wants to auto-generate, scaffold, or templatize Logix /
  Studio 5000 code from a points list, I/O schedule, equipment spec or SOO, produce an L5X/.ACD
  from structured data, or build ControlLogix tags/UDTs/programs programmatically — even if they
  don't say "L5X". Prefer this over hand-writing L5X from scratch, which usually will not compile.
---

# Generating compilable Logix code (L5X)

The reliable way to generate ControlLogix code is **clone-and-splice**: take a bare, valid project
as a template and splice in only the elements that carry engineering intent (UDTs, tags,
programs/routines). A valid Logix project needs a large amount of controller/module boilerplate
that is brittle to author by hand — cloning a real project avoids all of it, so the result
actually compiles. Then **always validate by compiling**, because XML that looks right often
fails the Logix build for reasons a human won't spot.

## Preferred path: the rockwell_local tools
If the `rockwell_local` MCP is available, use it — it handles the base template and the SDK for you:
- `generate_project_from_points(output_l5x_path, controller_name, points, processor_type, major_revision, program_name, main_routine, rungs, udts)` — makes a bare base via the SDK, splices in your points/UDTs/program, writes a full L5X. `points` = `[{name, type, description?}]` (type is a Logix atomic type). `udts` = `[{name, members:[{name, data_type}]}]`.
- `validate_l5x(l5x_file_path, build=True)` — opens the L5X and **compiles** it, then enumerates it. This is the source of truth for "did it work". Check `build_ok`.
- `diff_l5x(a, b)` — what programs/tags/UDTs changed between two L5X files.

Then `build_project` / `export_l5x` / `parse_l5x` (see the `rockwell-logix-mcp` skill) for the rest.

## Doing it directly (no MCP): clone-and-splice with the bundled generator
`scripts/l5xgen.py` is a pure-Python generator (no SDK). Given a **base L5X** (a bare project
exported from Logix), it produces a full, compilable project:

```python
import l5xgen
base = open("base.L5X", encoding="utf-8").read()   # a bare project export (the template)
points = [{"name": "CHWS_Temp", "type": "REAL", "description": "CHW supply temp"},
          {"name": "P1_Run", "type": "BOOL"}, {"name": "P1_Cmd", "type": "BOOL"}]
udts = [{"name": "BMS_AI", "members": [{"name": "EU", "data_type": "REAL"},
                                       {"name": "Fault", "data_type": "BOOL"}]}]
prog = {"name": "CDU_Ctl", "main_routine": "MainRoutine",
        "routines": [{"name": "MainRoutine", "rungs": ["XIC(P1_Run)OTE(P1_Cmd);"]}]}
l5x = l5xgen.inject(base, tags=l5xgen.tags_from_points(points), udts=udts,
                    programs=[prog], schedule=["CDU_Ctl"])
open("out.L5X", "w", encoding="utf-8").write(l5x)
```

You need a base template. Get one by exporting a bare SDK project (`create_new_project` →
`save_as(..., detailed_l5x=True)`), or use `generate_project_from_points`, which does this for you.

## The L5X element shapes (for hand-authoring fragments)
Grounded in a real v38 export. Atomic types: BOOL, SINT, INT, DINT, LINT, USINT, UINT, UDINT,
ULINT, REAL, LREAL (integers use `Radix="Decimal"`, REAL/LREAL use `Radix="Float"`).

- **Tag** (atomic): `<Tag Name=".." TagType="Base" DataType="DINT" Radix="Decimal" Constant="false" ExternalAccess="Read/Write"><Data Format="L5K"><![CDATA[0]]></Data><Data Format="Decorated"><DataValue DataType="DINT" Radix="Decimal" Value="0"/></Data></Tag>`
- **UDT**: `<DataType Name=".." Family="NoFamily" Class="User"><Members><Member Name=".." DataType="REAL" Dimension="0" Radix="Float" Hidden="false" ExternalAccess="Read/Write"/>…</Members></DataType>`
- **Program**: `<Program Name=".." MainRoutineName="MainRoutine" Class="Standard" …><Routines><Routine Name="MainRoutine" Type="RLL"><RLLContent><Rung Number="0" Type="N"><Text><![CDATA[XIC(a)OTE(b);]]></Text></Rung></RLLContent></Routine></Routines></Program>`
- **Schedule** a program so it runs: add `<ScheduledProgram Name=".."/>` inside the task's `<ScheduledPrograms>`.

## Critical gotchas
- **Bare project.** A fresh SDK project has no MainProgram/MainRoutine. Always scaffold (and
  **schedule**) a program, or the controller does nothing and some builds warn.
- **Schedule the program.** An unscheduled program compiles but never executes.
- **Rung logic must reference real tags.** `OTE(Pump1_Cmd)` needs a `Pump1_Cmd` tag to exist, or
  the build fails. Generate tags before (or with) the logic that uses them.
- **Points → types.** Map DI/DO → BOOL, AI/AO → REAL (or INT for raw), per the point's signal.
- **Prove it compiles.** Treat `validate_l5x`/`build_project` success as the only real "done".
  A non-compiling L5X is not a deliverable.

## Mapping a points list
Each point becomes a controller tag. Keep names Logix-legal (letters, digits, underscore; start
with a letter). Put the engineering description in the tag `Description`. For structured signals
(analog with alarms, status words), define a UDT and make the tag that type.
