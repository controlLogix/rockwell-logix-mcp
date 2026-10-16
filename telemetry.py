"""Telemetry for BMS/EPMS points: an any-protocol data layer, a publisher, and a simulator.

Synthesize point values and publish them over a normalized Point/Sample model to pluggable sinks
(JSONL, MQTT, OPC UA). This drives dashboards, FAT/SAT and demos with no hardware. Live reads from a
Logix controller (EtherNet/IP) or an OPC UA server use the same Point model, so a simulated point and
a real one are interchangeable downstream.

Pure Python on the installed libs (asyncua, paho-mqtt, pycomm3). Imports of the protocol libs are
deferred into the functions that use them, so simulation works even if a protocol lib is absent.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass


@dataclass
class Point:
    name: str
    kind: str = "analog"      # analog | bool
    behavior: str = "sine"    # sine | ramp | walk | toggle | constant | random
    lo: float = 0.0
    hi: float = 100.0
    period_s: float = 10.0
    unit: str = ""
    description: str = ""


def _coerce(points) -> list[Point]:
    return [p if isinstance(p, Point) else Point(**p) for p in points]


def _value(p: Point, t: float, state: dict):
    if p.kind == "bool":
        if p.behavior == "toggle":
            return int((t // max(p.period_s, 1e-6)) % 2)
        if p.behavior == "random":
            return int(random.random() > 0.5)
        return int(state.get(p.name, 0))
    span = p.hi - p.lo
    per = max(p.period_s, 1e-6)
    if p.behavior == "sine":
        return round(p.lo + span * (0.5 + 0.5 * math.sin(2 * math.pi * t / per)), 4)
    if p.behavior == "ramp":
        return round(p.lo + span * ((t % per) / per), 4)
    if p.behavior == "walk":
        v = state.get(p.name, (p.lo + p.hi) / 2) + random.uniform(-span * 0.03, span * 0.03)
        v = min(p.hi, max(p.lo, v))
        state[p.name] = v
        return round(v, 4)
    if p.behavior == "constant":
        return round((p.lo + p.hi) / 2, 4)
    return round(random.uniform(p.lo, p.hi), 4)


def simulate(points, samples: int = 10, interval_s: float = 1.0, t0: float = 0.0) -> list[dict]:
    """Produce `samples` time steps of values for each point. Returns a flat list of
    {t, name, value, unit, kind} records (one per point per step)."""
    pts = _coerce(points)
    state: dict = {}
    out: list[dict] = []
    for i in range(samples):
        t = t0 + i * interval_s
        for p in pts:
            out.append({"t": round(t, 3), "name": p.name, "value": _value(p, t, state),
                        "unit": p.unit, "kind": p.kind})
    return out


def latest_values(points, t: float = 0.0) -> dict:
    pts = _coerce(points)
    state: dict = {}
    return {p.name: _value(p, t, state) for p in pts}


# --- sinks -------------------------------------------------------------------------
def to_jsonl(samples, path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")
    return path


def mqtt_publish(samples, host="127.0.0.1", port=1883, topic_prefix="bms/telemetry", keepalive=10) -> int:
    """Publish each sample as JSON to <topic_prefix>/<name>. Needs a reachable MQTT broker."""
    import paho.mqtt.client as mqtt
    c = mqtt.Client()
    c.connect(host, port, keepalive=keepalive)
    n = 0
    for s in samples:
        c.publish(f"{topic_prefix}/{s['name']}", json.dumps(s))
        n += 1
    c.disconnect()
    return n


# --- OPC UA publish + read round-trip (self-contained; no hardware) -----------------
async def opcua_roundtrip(points, samples: int = 5,
                          endpoint: str = "opc.tcp://127.0.0.1:48401/bms/") -> list[dict]:
    """Start an in-process OPC UA server, publish the latest simulated value for each point, then
    read them back with a client. Proves the simulate->publish->read pipeline with no hardware."""
    from asyncua import Server, Client
    pts = _coerce(points)
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    idx = await server.register_namespace("bms")
    obj = await server.nodes.objects.add_object(idx, "Points")
    nodes = {}
    for p in pts:
        nodes[p.name] = await obj.add_variable(idx, p.name, 0.0 if p.kind == "analog" else False)
        await nodes[p.name].set_writable()
    await server.start()
    read_back: list[dict] = []
    try:
        latest = latest_values(pts, t=(samples - 1) * 1.0)
        for p in pts:
            v = latest[p.name]
            await nodes[p.name].write_value(float(v) if p.kind == "analog" else bool(v))
        async with Client(url=endpoint) as client:
            for p in pts:
                node = await client.nodes.root.get_child(
                    ["0:Objects", f"{idx}:Points", f"{idx}:{p.name}"])
                read_back.append({"name": p.name, "value": await node.read_value()})
    finally:
        await server.stop()
    return read_back


# --- EtherNet/IP source (live read; needs a controller) -----------------------------
def read_logix_tags(plc_ip: str, tag_names: list[str]) -> dict:
    """Read live tags from a Logix controller over EtherNet/IP (pycomm3). Requires hardware."""
    from pycomm3 import LogixDriver
    out = {}
    with LogixDriver(plc_ip) as plc:
        for t in tag_names:
            out[t] = plc.read(t).value
    return out
