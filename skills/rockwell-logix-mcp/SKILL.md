---
name: rockwell-logix-mcp
description: >-
  Operate the rockwell_local MCP server to do offline Rockwell / ControlLogix engineering through
  the Logix Designer SDK — create/open/convert projects, build/compile, import and export L5X,
  enumerate project contents, and read/write tags. Use this whenever the user wants to
  programmatically create, build, modify, inspect, or generate a Studio 5000 / Logix Designer
  project (.ACD or .L5X), author ControlLogix code, or work with the rockwell_local tools — even if
  they don't name the MCP by name. Not for online download to physical controllers (a later phase).
---

# Operating the rockwell_local MCP (offline Logix engineering)

`rockwell_local` wraps the Logix Designer SDK as MCP tools, running on the Windows VM next to
Studio 5000 and `LdSdkService`. This skill is about using those tools well for **offline** work:
authoring, building, L5X round-tripping, enumeration, and tag values. If the environment isn't set
up (import errors, `operation has timed out`), fix that first with the `rockwell-sdk-setup` skill.

## The handle model (read this first)
Tool calls are stateless, but a Logix project is a live, slow-to-open handle. So the server keeps
an **open-project registry keyed by absolute path**:
- `create_project` / `open_project` / `convert_project` open a project and cache the handle.
- Every other tool acts on an already-open project via its `project_file_path`.
- `close_project` releases it. `list_open_projects` shows what's open.

Always open (or create) a project before acting on it, and close it when done. Opening an `.ACD`
from an older Logix revision triggers a conversion and can take a while.

## Tool catalog
- **Diagnostics:** `server_info`, `get_processor_types(major_revision)` — list valid controller
  catalog numbers for a revision (e.g. 38). `get_processor_types` needs no open project.
- **Lifecycle:** `create_project(path, major_revision, processor_type, controller_name)`,
  `open_project(path)`, `convert_project(path, destination_revision)`, `save_project(path)`,
  `save_project_as(path, save_path, force, detailed_l5x)`, `export_l5x(path, l5x_path)`,
  `change_controller_type(path, processor_type)`, `close_project(path)`, `list_open_projects()`.
- **Build:** `build_project(path, target)` — `target` is `DEFAULT_TARGET` | `ECHO_CONTROLLER` |
  `PHYSICAL_CONTROLLER`; defaults to `DEFAULT_TARGET`. Needs Logix v37+.
- **L5X import/export:** `partial_export_xml(path, element_path, output_file)`,
  `partial_import_xml(path, element_path, xml_file, collision_option, continue_on_errors)`,
  `partial_import_rungs(path, element_path, insert_position, replace_count, xml_file, online_import_option)`.
- **Tags:** `read_tag(path, tag_path, data_type, mode)`, `write_tag(path, tag_path, value, data_type, mode)`.
- **Enumerate:** `parse_l5x(l5x_file_path)` — pure-XML listing of controller, programs+routines,
  controller/program tags, AOIs, UDTs, modules.

## Critical gotcha: new projects are bare
`create_project` (SDK `create_new_project`) produces a **bare** project — a Controller and an empty
`MainTask`, but **no MainProgram, no MainRoutine, and no tags**. This is unlike the Studio 5000 GUI,
which scaffolds a MainProgram for you. So before you can add logic, **create the program/routine by
partial-importing an L5X**. Don't assume `MainProgram` exists.

## The L5X authoring channel
The SDK has **no rich ladder/ST object model** and **cannot enumerate** a project. Real authoring
and all discovery go through **L5X XML**:
- To add programs, routines, AOIs, UDTs, or tags: build the L5X and `partial_import_xml` at the
  right `element_path` (e.g. `Controller/Programs`), or `partial_import_rungs` into a routine.
- To inspect what's in a project: `export_l5x` then `parse_l5x`. There is no "list tags" tool
  because the SDK can't enumerate — `parse_l5x` fills that gap.

## Tags: types and modes
`data_type` is one of `BOOL, SINT, INT, DINT, LINT, USINT, UINT, UDINT, ULINT, REAL, LREAL, STRING`.
`mode` is `OFFLINE` (project value) or `ONLINE` (live — requires an online session, a later phase;
avoid for now). `read_tag`/`write_tag` need the **exact tag path** — discover paths with `parse_l5x`,
not by guessing. Offline writes change the in-memory project; call `save_project` to persist them.

## Common workflows

**Create → add logic → build → export:**
1. `create_project(path, 38, "1756-L85E", "CDU01")`
2. Build a MainProgram/MainRoutine L5X, then `partial_import_xml(path, "Controller/Programs", program.l5x, "OVERWRITE_ON_COLL")`
3. `build_project(path)`
4. `export_l5x(path, out.l5x)` (then `parse_l5x` to confirm contents)
5. `save_project(path)`; `close_project(path)`

**Inspect an existing project:**
1. `open_project(path)` → `export_l5x(path, tmp.l5x)` → `parse_l5x(tmp.l5x)`
2. Use the enumerated tag paths for any `read_tag` calls. `close_project` when done.

**Migrate a controller / upgrade a revision:** `change_controller_type` for the catalog number,
`convert_project` for the Logix revision — both act on the open handle.

## Scope boundary (safety)
This skill covers **offline** work only. Online session (`go_online`), **download/upload to
hardware**, controller **mode changes**, and SD-card images are consequential on physical
controllers and belong to a later, confirm-gated phase — do not improvise them here.
