import xml.etree.ElementTree as ET

import l5xgen


BASE = """<?xml version="1.0" encoding="UTF-8"?>
<RSLogix5000Content SchemaRevision="1.0">
<Controller Name="T" ProcessorType="1756-L85E" MajorRev="38">
<DataTypes/>
<Tags/>
<Tasks/>
<Programs/>
</Controller>
</RSLogix5000Content>
"""


def test_tag_l5x_analog_vs_bool():
    real = l5xgen.tag_l5x("CHWS_Temp", "REAL")
    assert 'DataType="REAL"' in real and 'Radix="Float"' in real
    assert '<DataValue DataType="REAL"' in real
    boolt = l5xgen.tag_l5x("Pump1_Run", "BOOL")
    assert 'DataType="BOOL"' in boolt and 'Radix="Decimal"' in boolt


def test_datatype_l5x_has_members():
    udt = l5xgen.datatype_l5x("BMS_AI", [{"name": "EU", "data_type": "REAL"},
                                         {"name": "Fault", "data_type": "BOOL"}])
    assert 'Class="User"' in udt and "<Members>" in udt
    assert '<Member Name="EU"' in udt and '<Member Name="Fault"' in udt


def test_program_l5x_has_routine_and_rung():
    prog = l5xgen.program_l5x("Ctl", "MainRoutine",
                              routines=[{"name": "MainRoutine", "rungs": ["XIC(a)OTE(b);"]}])
    assert 'MainRoutineName="MainRoutine"' in prog
    assert '<Routine Name="MainRoutine" Type="RLL">' in prog
    assert "XIC(a)OTE(b);" in prog


def test_tags_from_points_mapping():
    out = l5xgen.tags_from_points([{"name": "T1", "type": "REAL", "description": "d"},
                                   {"name": "B1", "type": "BOOL"}])
    assert out[0]["name"] == "T1" and out[0]["data_type"] == "REAL"
    assert out[1]["data_type"] == "BOOL"


def test_inject_wellformed_and_contains_elements():
    out = l5xgen.inject(
        BASE,
        tags=l5xgen.tags_from_points([{"name": "Pump1_Run", "type": "BOOL"}]),
        udts=[{"name": "BMS_AI", "members": [{"name": "EU", "data_type": "REAL"}]}],
        programs=[{"name": "Ctl", "main_routine": "MainRoutine",
                   "routines": [{"name": "MainRoutine", "rungs": []}]}],
        schedule=["Ctl"],
    )
    root = ET.fromstring(out)  # must be well-formed XML
    assert any(d.get("Name") == "BMS_AI" for d in root.iter("DataType"))
    assert any(t.get("Name") == "Pump1_Run" for t in root.iter("Tag"))
    assert any(p.get("Name") == "Ctl" for p in root.iter("Program"))


def test_inject_schedule_creates_maintask_on_empty_tasks():
    # regression: a bare base has <Tasks/> (no MainTask); schedule must CREATE one, not no-op.
    out = l5xgen.inject(
        BASE,
        programs=[{"name": "Ctl", "main_routine": "MainRoutine",
                   "routines": [{"name": "MainRoutine", "rungs": []}]}],
        schedule=["Ctl"],
    )
    root = ET.fromstring(out)
    tasks = [t.get("Name") for t in root.iter("Task")]
    assert "MainTask" in tasks
    scheduled = [s.get("Name") for s in root.iter("ScheduledProgram")]
    assert "Ctl" in scheduled
