---
name: rockwell-telemetry
description: >-
  Move BMS/EPMS point data over an any-protocol layer with the rockwell_local MCP — simulate
  synthetic telemetry with no hardware, publish it (JSONL, MQTT, OPC UA), and read live tags from a
  Logix controller. Use this whenever the user wants to generate, stream, publish, mock, simulate or
  replay telemetry / point data, feed a dashboard or HMI with test data, drive a FAT/SAT without a
  PLC, or wire BMS points to MQTT / OPC UA / a time-series store — even if they don't say "telemetry".
---

# Telemetry: simulate, publish, read (any-protocol)

BMS/EPMS work constantly needs point data flowing — to dashboards, to a historian, into FAT/SAT
checks — and usually before any controller is on the bench. So the core idea here is a **normalized
Point model** that is the same whether a value is simulated or read live, with pluggable sinks and
sources. Simulate first (no hardware), publish over whatever protocol the consumer wants, and swap in
the live source later without changing anything downstream.

## The Point model
A point is `{name, kind, behavior, lo, hi, period_s, unit, description}`:
- `kind`: `analog` or `bool`.
- `behavior`: how a simulated value moves — `sine` (smooth cyclic, good for temps/flows), `ramp`,
  `walk` (random walk within range), `toggle` (bool on/off every period), `constant`, `random`.
- `lo`/`hi`: range; `period_s`: cycle time; `unit`/`description`: metadata that travels with samples.

A **sample** is `{t, name, value, unit, kind}` — identical for simulated and live data.

## Tools (rockwell_local)
- `simulate_telemetry(points, samples, interval_ms, output_jsonl)` — generate synthetic samples with
  no hardware. Returns a preview; writes JSONL if `output_jsonl` is set. Use this to produce test
  data for dashboards, HMIs, FAT/SAT, and demos.
- `telemetry_selftest(points, samples)` — **proves the pipeline without hardware**: spins up an
  in-process OPC UA server, publishes simulated values, and reads them back. The quickest way to
  show simulate → publish → read works end to end.
- `publish_telemetry_mqtt(points, samples, host, port, topic_prefix, interval_ms)` — publish simulated
  samples to an MQTT broker (one JSON message per sample at `<topic_prefix>/<name>`). Needs a
  reachable broker; returns a clear error if none is there.
- `read_logix_telemetry(plc_ip, tag_names)` — read **live** tags from a Logix controller over
  EtherNet/IP (pycomm3). Needs a reachable controller; same sample shape as simulated points.

The generator (`telemetry.py`) is pure Python on the installed libs (asyncua, paho-mqtt, pycomm3),
so simulation runs anywhere; only live/broker paths need the network peer.

## Typical flows
- **Dashboard/HMI test data:** define the points, `simulate_telemetry(..., output_jsonl=...)`, point
  the dashboard at the JSONL (or publish to MQTT/OPC UA and subscribe).
- **Prove it works with no PLC:** `telemetry_selftest(points)` → OPC UA round-trip.
- **Publish to the plant bus:** `publish_telemetry_mqtt` (Sparkplug/MQTT) or an OPC UA sink.
- **Go live:** replace the simulator with `read_logix_telemetry(plc_ip, tags)` — downstream is
  unchanged because the sample shape is identical.

## Picking behaviors (so simulated data looks real)
- Temperatures / pressures / flows → `sine` or `walk` within realistic `lo`/`hi`, `unit` set.
- Run/status/alarm bits → `toggle` (periodic) or `random`.
- Setpoints / constants → `constant`.
Keep ranges and units physically plausible; downstream alarming and charts depend on them.

## Honesty about what needs a peer
Simulation and the OPC UA self-test need no hardware or external broker. `publish_telemetry_mqtt`
needs a broker; `read_logix_telemetry` needs a controller. If those aren't available, say the data
was simulated (and self-tested over OPC UA) rather than implying a broker or PLC was involved.
