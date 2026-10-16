---
name: rockwell-build-commission
description: >-
  Safely take a Rockwell/ControlLogix project online and commission it with the rockwell_local MCP —
  resolve a FactoryTalk Linx comm path, go online, download, change controller mode, and upload. Use
  this whenever the user wants to connect to, download to, go online with, flash, commission, or
  change the mode of a Logix controller (physical or emulated), or asks how to push a Studio 5000
  project to a PLC. These operations act on live equipment, so this skill is mostly about doing it
  safely and in the right order — reach for it even when the user just says "push it to the PLC".
---

# Commissioning a Logix controller (online ops) — safely

Online operations act on a **real, running controller**. A download stops the running process and
overwrites its program; putting a controller in RUN energizes outputs and actuates equipment.
So the governing rule here is: **nothing consequential happens without an explicit, confirmed,
deliberate step.** The `rockwell_local` tools enforce this — the mutating ones are confirm-gated.

## The confirm-gate / dry-run model (how the tools behave)
`go_online`, `download`, `upload`, `upload_to_new_project`, and `change_controller_mode` take
`confirm` (default false). **Without `confirm=true` they return a dry-run plan and do NOT touch the
controller.** Always dry-run first, show the user exactly what will happen and to which controller,
get an explicit go-ahead, then re-call with `confirm=true`. Never pass `confirm=true` on your own
initiative — a human authorizes each download and each move to RUN, every time.

Read-only tools (`read_connected_state`, `read_controller_mode`, `get_comm_path`) and
`set_comm_path`/`build_comm_path`/`go_offline` are not gated — they don't change the controller.

## Prerequisites
- A **built** project (see `rockwell-logix-mcp` / `rockwell-l5x-codegen`). Build before you download.
- A **FactoryTalk activation** for Studio 5000 — online download generally requires it. (If none is
  present, offline work still succeeds but online will fail; resolve activation first.)
- Network reachability to the controller via FactoryTalk Linx, and the right comm path.

## Comm path
Build the CIP path, then set it on the open project:
1. `build_comm_path(driver, address, backplane_slot)` → e.g. `AB_ETHIP-1\192.168.1.20\Backplane\0`.
   `driver` is a FactoryTalk Linx driver name (`AB_ETHIP-1` = EtherNet/IP); `address` the controller
   IP/host; `backplane_slot` the CPU slot.
2. `set_comm_path(project_file_path, comm_path)`; confirm with `get_comm_path`.

## Commissioning sequence (download, then run)
1. `open_project` (or create+build) the project; be sure it builds clean.
2. `set_comm_path` to the target controller.
3. `go_online` — **dry-run, confirm, then `confirm=true`**. Check `read_connected_state`.
4. `read_controller_mode`. A download requires the controller in **PROGRAM**; if it is in RUN,
   that is exactly the dangerous moment — confirm with the user before changing mode.
5. `download` — **dry-run, confirm, then `confirm=true`.** This overwrites the controller program.
6. Verify: `read_controller_mode` / `read_connected_state`.
7. Only when the user explicitly authorizes starting the process:
   `change_controller_mode("RUN", confirm=true)`.

To pull what is on a controller: `upload` (into the open project) or `upload_to_new_project`
(fresh project from the controller) — both confirm-gated.

## Safety checklist before any download / RUN
- Right controller? Re-read the comm path aloud to the user (IP + slot).
- Is this equipment live / is anyone relying on the running program? Downloading stops it.
- Does the project build clean? Never download an unverified project.
- Is the move to RUN intended now, or should it stay in PROGRAM for checkout?
- For safety (GuardLogix) controllers, safety-lock/signature state matters — do not bypass it.

## Failure signatures
| Symptom | Likely cause | Action |
|---|---|---|
| `go_online` / `download` fails with a licensing/activation error | No FactoryTalk activation | Resolve activation in FactoryTalk Activation Manager |
| Cannot find the controller / comm path invalid | Linx driver/path wrong or no route | Check the driver name and address; browse in FactoryTalk Linx |
| `operation has timed out` on an online call | Environment/.NET wiring, or unreachable controller | See `rockwell-sdk-setup`; verify reachability |
| Download refused — controller in RUN with sources locked | Mode/lock state | Change mode (confirmed) or unlock per policy |

## Scope / honesty
If there is no controller, emulator (Logix Echo), or activation available, you can still set comm
paths, read offline connected-state, and dry-run every gated op — but you cannot truly go online or
download. Say so plainly rather than implying a controller was contacted.
