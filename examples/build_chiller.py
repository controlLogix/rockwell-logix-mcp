"""EXAMPLE (sanitized): set your own broker IP in BROKER_ADDR and MQTT user/password
before use. Generate the Rockwell chiller-simulation + MQTT standard as L5X and compile-validate it.

A ControlLogix/CompactLogix (5069-L306ERS2, v38) ST-first standard that:
  - UDT_Chiller       : a full BMS chiller point set (status, temps, pressures, flows,
                        electrical, capacity/efficiency, setpoints, runtime, alarms)
  - AOI_ChillerSim    : chiller CONTROLS (start/stop sequencing, capacity control, alarm
                        evaluation + fault latch) + light-physics PLANT model in one AOI
  - AOI_RealToStr     : REAL -> STRING (2 decimals); Logix has no native RTOS
  - AOI_JsonBuild     : UDT_Chiller -> a compact, grouped custom-structure JSON telemetry
                        payload (models the CODESYS PRO_JSON IEC_TO_JSON idea)
  - AOI_MqttConnect   : assemble an MQTT 3.1.1 CONNECT packet (username/password auth)
  - AOI_MqttPublish   : assemble an MQTT 3.1.1 PUBLISH (QoS 0) packet
  - socket transport  : PROGRAM-scope CIP-socket client (ENET-AT002) - Create/Open/Write
                        (CONNECT)/Read (CONNACK)/Write (PUBLISH loop)/Delete via CIP Generic
                        MSGs to the socket object 0x342, "Message to Self" path "1, 0",
                        UNCONNECTED (front Ethernet port). MESSAGE tags can't live in an AOI,
                        so this runs as a CASE state machine in MainRoutine.

Topic: bms/chiller/CH1/telemetry  Broker: <broker-ip>:1883. ~1 s cadence
(MainTask PERIODIC 1000 ms). Run on the VM (rockwell_local server.py + l5xgen.py present).
"""
import os
import re
import sys
import asyncio
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # point at the MCP server dir (holds l5xgen.py, server.py)
import l5xgen
import server as S

CONTROLLER = "ChillerSimCtl"
PROCESSOR = "5069-L306ERS2"
MAJOR_REV = 38
BROKER_ADDR = "192.0.2.10?port=1883"   # 8 + len(addr) bytes => OpenConnection source len
TOPIC = "bms/chiller/CH1/telemetry"

# --- string type for the JSON payload ----------------------------------------------
STRING_512 = {
    "name": "STRING_512",
    "family": "StringFamily",
    "members": [
        '<Member Name="LEN" DataType="DINT" Dimension="0" Radix="Decimal" Hidden="false"/>',
        '<Member Name="DATA" DataType="SINT" Dimension="512" Radix="ASCII" Hidden="false"/>',
    ],
}

# --- UDT_Chiller: the full BMS point set -------------------------------------------
UDT_CHILLER = {
    "name": "UDT_Chiller",
    "members": [
        # commands
        {"name": "Enable", "data_type": "BOOL"},
        {"name": "RunCmd", "data_type": "BOOL"},
        # status
        {"name": "Running", "data_type": "BOOL"},
        {"name": "Available", "data_type": "BOOL"},
        {"name": "Fault", "data_type": "BOOL"},
        {"name": "Alarm", "data_type": "BOOL"},
        {"name": "Warning", "data_type": "BOOL"},
        {"name": "State", "data_type": "DINT"},          # 0 Off 1 Start 2 Run 3 Stop 4 Fault
        # temperatures (degF)
        {"name": "CHWS_Temp", "data_type": "REAL"},
        {"name": "CHWR_Temp", "data_type": "REAL"},
        {"name": "CWS_Temp", "data_type": "REAL"},
        {"name": "CWR_Temp", "data_type": "REAL"},
        {"name": "EvapSatTemp", "data_type": "REAL"},
        {"name": "CondSatTemp", "data_type": "REAL"},
        {"name": "OilTemp", "data_type": "REAL"},
        {"name": "MotorTemp", "data_type": "REAL"},
        # pressures (psi)
        {"name": "EvapPress", "data_type": "REAL"},
        {"name": "CondPress", "data_type": "REAL"},
        {"name": "OilPress", "data_type": "REAL"},
        {"name": "DiffPress", "data_type": "REAL"},
        # flows (gpm)
        {"name": "EvapFlow", "data_type": "REAL"},
        {"name": "CondFlow", "data_type": "REAL"},
        # electrical
        {"name": "Power_kW", "data_type": "REAL"},
        {"name": "PercentRLA", "data_type": "REAL"},
        {"name": "LineCurrent", "data_type": "REAL"},
        {"name": "LineVoltage", "data_type": "REAL"},
        {"name": "PowerFactor", "data_type": "REAL"},
        {"name": "Energy_kWh", "data_type": "REAL"},
        # capacity / efficiency
        {"name": "LoadPct", "data_type": "REAL"},
        {"name": "CompSpeedPct", "data_type": "REAL"},
        {"name": "CapacityTons", "data_type": "REAL"},
        {"name": "kWperTon", "data_type": "REAL"},
        {"name": "COP", "data_type": "REAL"},
        # setpoints
        {"name": "CHW_Setpoint", "data_type": "REAL"},
        {"name": "DemandLimit", "data_type": "REAL"},
        {"name": "ActiveSetpoint", "data_type": "REAL"},
        # runtime
        {"name": "RunHours", "data_type": "REAL"},
        {"name": "Starts", "data_type": "DINT"},
        # alarms
        {"name": "Alm_LowEvapTemp", "data_type": "BOOL"},
        {"name": "Alm_HighCondPress", "data_type": "BOOL"},
        {"name": "Alm_LowEvapFlow", "data_type": "BOOL"},
        {"name": "Alm_LowOilPress", "data_type": "BOOL"},
        {"name": "Alm_HighMotorTemp", "data_type": "BOOL"},
        {"name": "Alm_HighCurrent", "data_type": "BOOL"},
        {"name": "Alm_PhaseLoss", "data_type": "BOOL"},
    ],
}

# --- AOI_RealToStr: REAL -> STRING (2 decimals) ------------------------------------
AOI_REALTOSTR = {
    "name": "AOI_RealToStr",
    "description": "Formats a REAL as a string with 2 decimals (Logix has no native RTOS).",
    "parameters": [
        {"name": "In", "data_type": "REAL", "usage": "Input", "required": True},
        {"name": "Out", "data_type": "STRING", "usage": "InOut", "required": True},
    ],
    "local_tags": [
        {"name": "scaled", "data_type": "DINT"},
        {"name": "whole", "data_type": "DINT"},
        {"name": "frac", "data_type": "DINT"},
        {"name": "neg", "data_type": "BOOL"},
        {"name": "sWhole", "data_type": "STRING"},
        {"name": "sFrac", "data_type": "STRING"},
        {"name": "s1", "data_type": "STRING"},
        {"name": "s2", "data_type": "STRING"},
        {"name": "lit", "data_type": "STRING"},
    ],
    "st_lines": [
        "// PM007: string literals are valid in ':=' only; CONCAT needs TAG operands, so stage literals.",
        "scaled := TRUNC(In * 100.0);",
        "neg := 0;",
        "IF scaled < 0 THEN neg := 1; scaled := ABS(scaled); END_IF;",
        "whole := scaled / 100;",
        "frac := scaled MOD 100;",
        "DTOS(whole, sWhole);",
        "DTOS(frac, sFrac);",
        "lit := '.';",
        "CONCAT(sWhole, lit, s1);",
        "IF frac < 10 THEN",
        "    lit := '0';",
        "    CONCAT(s1, lit, s2);",
        "    CONCAT(s2, sFrac, s1);",
        "ELSE",
        "    CONCAT(s1, sFrac, s2);",
        "    s1 := s2;",
        "END_IF;",
        "IF neg THEN",
        "    lit := '-';",
        "    CONCAT(lit, s1, Out);",
        "ELSE",
        "    Out := s1;",
        "END_IF;",
    ],
}

# --- AOI_JsonBuild: UDT_Chiller -> custom grouped JSON telemetry --------------------
# Custom structure (compact keys so the whole MQTT packet fits the 462-byte unconnected
# WriteSocket limit): {"id","seq","st","run","alm",
#   "t":{chws,chwr,cws,cwr}, "f":{ev,cd},
#   "p":{kw,rla,a,tons,kpt}, "sp":{chw,dmd}, "rt":{hrs,strt}}
def _seed(lit):   # start Json from a staged literal + the seq number
    return [f"lit := '{lit}';", "d := Seq;", "DTOS(d, sNum);", "CONCAT(lit, sNum, Json);"]
def _lit(s):      # append a literal
    return [f"lit := '{s}';", "CONCAT(Json, lit, Json);"]
def _real(member):
    return [f"AOI_RealToStr(rts, Chiller.{member}, rstr);", "CONCAT(Json, rstr, Json);"]
def _int(member):
    return [f"d := Chiller.{member};", "DTOS(d, sNum);", "CONCAT(Json, sNum, Json);"]
def _bool(member):
    return [f"IF Chiller.{member} THEN d := 1; ELSE d := 0; END_IF;",
            "DTOS(d, sNum);", "CONCAT(Json, sNum, Json);"]

_JSON_ST = []
_JSON_ST += _seed('{"id":"CH1","seq":')
_JSON_ST += _lit(',"st":') + _int("State")
_JSON_ST += _lit(',"run":') + _bool("Running")
_JSON_ST += _lit(',"alm":') + _bool("Alarm")
_JSON_ST += _lit(',"t":{"chws":') + _real("CHWS_Temp")
_JSON_ST += _lit(',"chwr":') + _real("CHWR_Temp")
_JSON_ST += _lit(',"cws":') + _real("CWS_Temp")
_JSON_ST += _lit(',"cwr":') + _real("CWR_Temp")
_JSON_ST += _lit('},"f":{"ev":') + _real("EvapFlow")
_JSON_ST += _lit(',"cd":') + _real("CondFlow")
_JSON_ST += _lit('},"p":{"kw":') + _real("Power_kW")
_JSON_ST += _lit(',"rla":') + _real("PercentRLA")
_JSON_ST += _lit(',"a":') + _real("LineCurrent")
_JSON_ST += _lit(',"tons":') + _real("CapacityTons")
_JSON_ST += _lit(',"kpt":') + _real("kWperTon")
_JSON_ST += _lit('},"sp":{"chw":') + _real("ActiveSetpoint")
_JSON_ST += _lit(',"dmd":') + _real("DemandLimit")
_JSON_ST += _lit('},"rt":{"hrs":') + _real("RunHours")
_JSON_ST += _lit(',"strt":') + _int("Starts")
_JSON_ST += _lit('}}')

AOI_JSONBUILD = {
    "name": "AOI_JsonBuild",
    "description": "Serializes UDT_Chiller to a compact grouped JSON telemetry payload.",
    "parameters": [
        {"name": "Chiller", "data_type": "UDT_Chiller", "usage": "InOut", "required": True},
        {"name": "Json", "data_type": "STRING_512", "usage": "InOut", "required": True},
        {"name": "Seq", "data_type": "DINT", "usage": "Input", "required": False},
    ],
    "local_tags": [
        {"name": "rts", "data_type": "AOI_RealToStr"},
        {"name": "rstr", "data_type": "STRING"},
        {"name": "sNum", "data_type": "STRING"},
        {"name": "lit", "data_type": "STRING"},
        {"name": "d", "data_type": "DINT"},
    ],
    "st_lines": _JSON_ST,
}

# --- AOI_ChillerSim: controls (sequencing + alarms + capacity) + plant physics ------
AOI_CHILLERSIM = {
    "name": "AOI_ChillerSim",
    "description": "Chiller controls (start/stop, capacity, alarms, fault latch) + light-physics plant model.",
    "parameters": [
        {"name": "Enable", "data_type": "BOOL", "usage": "Input"},
        {"name": "RunCmd", "data_type": "BOOL", "usage": "Input"},
        {"name": "CHW_Setpoint", "data_type": "REAL", "usage": "Input", "default": "44.0"},
        {"name": "DemandLimit", "data_type": "REAL", "usage": "Input", "default": "100.0"},
        {"name": "LoadDemand", "data_type": "REAL", "usage": "Input", "default": "0.0"},
        {"name": "DeltaT", "data_type": "REAL", "usage": "Input", "default": "1.0"},
        {"name": "Chiller", "data_type": "UDT_Chiller", "usage": "InOut", "required": True},
    ],
    "local_tags": [
        {"name": "phase", "data_type": "REAL"},
        {"name": "StartTmr", "data_type": "REAL"},
        {"name": "StopTmr", "data_type": "REAL"},
        {"name": "target", "data_type": "REAL"},
        {"name": "eff", "data_type": "REAL"},
        {"name": "RatedkW", "data_type": "REAL", "default": "450.0"},
        {"name": "RatedGPM", "data_type": "REAL", "default": "1200.0"},
        {"name": "RatedTons", "data_type": "REAL", "default": "500.0"},
        {"name": "RatedAmps", "data_type": "REAL", "default": "560.0"},
    ],
    "st_lines": [
        "// ---- command passthrough + rated-value guards ----",
        "Chiller.Enable := Enable;",
        "Chiller.RunCmd := RunCmd;",
        "Chiller.CHW_Setpoint := CHW_Setpoint;",
        "Chiller.DemandLimit := DemandLimit;",
        "Chiller.ActiveSetpoint := CHW_Setpoint;",
        "IF RatedkW <= 0.0 THEN RatedkW := 450.0; END_IF;",
        "IF RatedGPM <= 0.0 THEN RatedGPM := 1200.0; END_IF;",
        "IF RatedTons <= 0.0 THEN RatedTons := 500.0; END_IF;",
        "IF RatedAmps <= 0.0 THEN RatedAmps := 560.0; END_IF;",
        "phase := phase + DeltaT;",
        "IF phase > 100000.0 THEN phase := 0.0; END_IF;",
        "// ---- availability ----",
        "IF Enable AND NOT Chiller.Fault THEN Chiller.Available := 1; ELSE Chiller.Available := 0; END_IF;",
        "// ---- start/stop state machine ----",
        "IF Chiller.Fault THEN Chiller.State := 4; END_IF;",
        "IF Chiller.State = 0 THEN",
        "    IF Enable AND RunCmd AND Chiller.Available THEN",
        "        Chiller.State := 1;",
        "        StartTmr := 0.0;",
        "        Chiller.Starts := Chiller.Starts + 1;",
        "    END_IF;",
        "ELSIF Chiller.State = 1 THEN",
        "    StartTmr := StartTmr + DeltaT;",
        "    IF NOT (Enable AND RunCmd) THEN Chiller.State := 3; StopTmr := 0.0;",
        "    ELSIF StartTmr >= 5.0 THEN Chiller.State := 2; END_IF;",
        "ELSIF Chiller.State = 2 THEN",
        "    IF NOT (Enable AND RunCmd) THEN Chiller.State := 3; StopTmr := 0.0; END_IF;",
        "ELSIF Chiller.State = 3 THEN",
        "    StopTmr := StopTmr + DeltaT;",
        "    IF StopTmr >= 3.0 THEN Chiller.State := 0; END_IF;",
        "ELSIF Chiller.State = 4 THEN",
        "    IF NOT Enable THEN Chiller.Fault := 0; Chiller.State := 0; END_IF;",
        "END_IF;",
        "IF Chiller.State = 2 THEN Chiller.Running := 1; ELSE Chiller.Running := 0; END_IF;",
        "// ---- capacity control (ramp load toward demand, clamped by demand limit) ----",
        "target := LoadDemand;",
        "IF target > Chiller.DemandLimit THEN target := Chiller.DemandLimit; END_IF;",
        "IF Chiller.Running THEN",
        "    IF Chiller.LoadPct < target THEN Chiller.LoadPct := Chiller.LoadPct + 5.0 * DeltaT; END_IF;",
        "    IF Chiller.LoadPct > target THEN Chiller.LoadPct := Chiller.LoadPct - 5.0 * DeltaT; END_IF;",
        "    IF Chiller.LoadPct < 15.0 THEN Chiller.LoadPct := 15.0; END_IF;",
        "    IF Chiller.LoadPct > 100.0 THEN Chiller.LoadPct := 100.0; END_IF;",
        "ELSE",
        "    Chiller.LoadPct := 0.0;",
        "END_IF;",
        "Chiller.CompSpeedPct := Chiller.LoadPct;",
        "Chiller.PercentRLA := Chiller.LoadPct * 1.05;",
        "// ---- electrical model ----",
        "IF Chiller.Running THEN",
        "    eff := 0.55 + 0.45 * (Chiller.LoadPct / 100.0);",
        "    Chiller.Power_kW := RatedkW * (Chiller.LoadPct / 100.0) * eff + 3.0 * SIN(phase / 5.0);",
        "    Chiller.LineCurrent := RatedAmps * (Chiller.PercentRLA / 100.0) + 2.0 * SIN(phase / 6.0);",
        "    Chiller.LineVoltage := 480.0 + 3.0 * SIN(phase / 9.0);",
        "    Chiller.PowerFactor := 0.84 + 0.10 * (Chiller.LoadPct / 100.0);",
        "ELSE",
        "    Chiller.Power_kW := 0.0;",
        "    Chiller.LineCurrent := 0.0;",
        "    Chiller.LineVoltage := 480.0;",
        "    Chiller.PowerFactor := 0.0;",
        "END_IF;",
        "Chiller.Energy_kWh := Chiller.Energy_kWh + Chiller.Power_kW * DeltaT / 3600.0;",
        "// ---- capacity / efficiency ----",
        "IF Chiller.Running THEN",
        "    Chiller.CapacityTons := RatedTons * (Chiller.LoadPct / 100.0) + 2.0 * SIN(phase / 7.0);",
        "ELSE",
        "    Chiller.CapacityTons := 0.0;",
        "END_IF;",
        "IF Chiller.CapacityTons > 1.0 THEN",
        "    Chiller.kWperTon := Chiller.Power_kW / Chiller.CapacityTons;",
        "    Chiller.COP := 3.517 / Chiller.kWperTon;",
        "ELSE",
        "    Chiller.kWperTon := 0.0;",
        "    Chiller.COP := 0.0;",
        "END_IF;",
        "// ---- flows ----",
        "IF Chiller.Running THEN",
        "    Chiller.EvapFlow := RatedGPM + 20.0 * SIN(phase / 7.0);",
        "    Chiller.CondFlow := RatedGPM * 1.2 + 25.0 * SIN(phase / 6.0);",
        "ELSE",
        "    Chiller.EvapFlow := 0.0;",
        "    Chiller.CondFlow := 0.0;",
        "END_IF;",
        "// ---- temperatures ----",
        "Chiller.CHWS_Temp := Chiller.ActiveSetpoint + (Chiller.LoadPct / 100.0) * 1.5 + 0.3 * SIN(phase / 4.0);",
        "Chiller.CHWR_Temp := Chiller.CHWS_Temp + 2.0 + (Chiller.LoadPct / 100.0) * 10.0;",
        "Chiller.CWS_Temp := 85.0 + (Chiller.LoadPct / 100.0) * 2.0 + 0.3 * SIN(phase / 8.0);",
        "Chiller.CWR_Temp := Chiller.CWS_Temp + 5.0 + (Chiller.LoadPct / 100.0) * 5.0;",
        "Chiller.EvapSatTemp := Chiller.CHWS_Temp - 4.0;",
        "Chiller.CondSatTemp := Chiller.CWR_Temp + 8.0;",
        "IF Chiller.Running THEN",
        "    Chiller.OilTemp := 120.0 + (Chiller.LoadPct / 100.0) * 20.0;",
        "    Chiller.MotorTemp := 150.0 + (Chiller.LoadPct / 100.0) * 60.0;",
        "ELSE",
        "    Chiller.OilTemp := 80.0;",
        "    Chiller.MotorTemp := 80.0;",
        "END_IF;",
        "// ---- pressures ----",
        "Chiller.EvapPress := 60.0 + (Chiller.EvapSatTemp - 34.0) * 1.2;",
        "Chiller.CondPress := 120.0 + (Chiller.CondSatTemp - 95.0) * 2.0;",
        "IF Chiller.Running THEN Chiller.OilPress := Chiller.CondPress - Chiller.EvapPress + 25.0; ELSE Chiller.OilPress := 0.0; END_IF;",
        "Chiller.DiffPress := Chiller.CondPress - Chiller.EvapPress;",
        "// ---- alarm evaluation ----",
        "IF Chiller.EvapSatTemp < 33.0 THEN Chiller.Alm_LowEvapTemp := 1; ELSE Chiller.Alm_LowEvapTemp := 0; END_IF;",
        "IF Chiller.CondPress > 180.0 THEN Chiller.Alm_HighCondPress := 1; ELSE Chiller.Alm_HighCondPress := 0; END_IF;",
        "IF Chiller.Running AND (Chiller.EvapFlow < 0.5 * RatedGPM) THEN Chiller.Alm_LowEvapFlow := 1; ELSE Chiller.Alm_LowEvapFlow := 0; END_IF;",
        "IF Chiller.Running AND (Chiller.OilPress < 15.0) THEN Chiller.Alm_LowOilPress := 1; ELSE Chiller.Alm_LowOilPress := 0; END_IF;",
        "IF Chiller.MotorTemp > 250.0 THEN Chiller.Alm_HighMotorTemp := 1; ELSE Chiller.Alm_HighMotorTemp := 0; END_IF;",
        "IF Chiller.PercentRLA > 105.0 THEN Chiller.Alm_HighCurrent := 1; ELSE Chiller.Alm_HighCurrent := 0; END_IF;",
        "Chiller.Alm_PhaseLoss := 0;",
        "IF Chiller.Alm_LowEvapTemp OR Chiller.Alm_HighCondPress OR Chiller.Alm_LowEvapFlow OR Chiller.Alm_LowOilPress OR Chiller.Alm_HighMotorTemp OR Chiller.Alm_HighCurrent OR Chiller.Alm_PhaseLoss THEN",
        "    Chiller.Alarm := 1;",
        "ELSE",
        "    Chiller.Alarm := 0;",
        "END_IF;",
        "IF Chiller.PercentRLA > 95.0 OR Chiller.CondPress > 160.0 THEN Chiller.Warning := 1; ELSE Chiller.Warning := 0; END_IF;",
        "IF Chiller.Alm_LowEvapTemp OR Chiller.Alm_HighCondPress OR Chiller.Alm_LowOilPress THEN Chiller.Fault := 1; END_IF;",
        "// ---- runtime ----",
        "IF Chiller.Running THEN Chiller.RunHours := Chiller.RunHours + DeltaT / 3600.0; END_IF;",
    ],
}

# --- Socket interface UDTs (ENET-AT002) --------------------------------------------
UDT_SOCKADDR = {"name": "UDT_SockAddr", "members": [
    {"name": "Family", "data_type": "INT"},
    {"name": "Port", "data_type": "INT"},
    {"name": "Addr", "data_type": "DINT"},
]}
UDT_SOCKCREATE = {"name": "UDT_SockCreate", "members": [
    {"name": "SockType", "data_type": "DINT"},           # 1=TCP, 2=UDP
    {"name": "Addr", "data_type": "UDT_SockAddr"},
]}
UDT_SOCKOPEN = {"name": "UDT_SockOpen", "members": [
    {"name": "Timeout", "data_type": "DINT"},
    {"name": "DestAddr", "data_type": "STRING"},          # "ip?port=n"; source len = 8 + chars
]}
UDT_SOCKWRITE = {"name": "UDT_SockWrite", "members": [
    {"name": "Timeout", "data_type": "DINT"},
    {"name": "ToAddr", "data_type": "UDT_SockAddr"},       # zeros for TCP
    {"name": "BufLen", "data_type": "DINT"},
    {"name": "Buf", "data_type": "SINT", "dimension": 462},   # unconnected WriteSocket data cap
]}
UDT_SOCKREAD = {"name": "UDT_SockRead", "members": [
    {"name": "Timeout", "data_type": "DINT"},
    {"name": "BufLen", "data_type": "DINT"},
]}
UDT_SOCKRXRESULT = {"name": "UDT_SockRxResult", "members": [
    {"name": "FromAddr", "data_type": "UDT_SockAddr"},
    {"name": "BufLen", "data_type": "DINT"},
    {"name": "Buf", "data_type": "SINT", "dimension": 100},
]}
UDT_TXBUFFER = {"name": "UDT_TxBuffer", "members": [
    {"name": "Len", "data_type": "DINT"},
    {"name": "Data", "data_type": "SINT", "dimension": 462},
]}
UDT_ANYCFG = {"name": "UDT_AnyCfg", "members": [
    {"name": "DestAddr", "data_type": "STRING"},   # "ip?port=n"
    {"name": "Timeout", "data_type": "DINT"},
]}

# --- AOI_MqttConnect: assemble an MQTT 3.1.1 CONNECT (username/password) packet ------
AOI_MQTTCONNECT = {
    "name": "AOI_MqttConnect",
    "description": "Builds an MQTT 3.1.1 CONNECT packet (clean session, username + password) into Tx.",
    "parameters": [
        {"name": "ClientId", "data_type": "STRING", "usage": "InOut", "required": True},
        {"name": "User", "data_type": "STRING", "usage": "InOut", "required": True},
        {"name": "Pass", "data_type": "STRING", "usage": "InOut", "required": True},
        {"name": "KeepAlive", "data_type": "DINT", "usage": "Input", "default": "60"},
        {"name": "Tx", "data_type": "UDT_TxBuffer", "usage": "InOut", "required": True},
    ],
    "local_tags": [
        {"name": "idx", "data_type": "DINT"},
        {"name": "e", "data_type": "DINT"},
        {"name": "rem", "data_type": "DINT"},
        {"name": "clen", "data_type": "DINT"},
        {"name": "ulen", "data_type": "DINT"},
        {"name": "plen", "data_type": "DINT"},
        {"name": "pn", "data_type": "STRING"},
    ],
    "st_lines": [
        "// MQTT 3.1.1 CONNECT: fixed header 16#10, remaining length (CONNECT < 128B = 1 byte),",
        "// variable header (protocol name MQTT, level 4, flags 16#C2, keepalive), payload (id/user/pass)",
        "Tx.Data[0] := 16#10;",
        "idx := 2;",
        "Tx.Data[idx] := 0;",
        "idx := idx + 1;",
        "Tx.Data[idx] := 4;",
        "idx := idx + 1;",
        "pn := 'MQTT';",
        "COP(pn.DATA[0], Tx.Data[idx], 4);",
        "idx := idx + 4;",
        "Tx.Data[idx] := 4;",
        "idx := idx + 1;",
        "Tx.Data[idx] := -62;",
        "idx := idx + 1;",
        "e := KeepAlive / 256;",
        "Tx.Data[idx] := e;",
        "idx := idx + 1;",
        "e := KeepAlive MOD 256;",
        "IF e > 127 THEN Tx.Data[idx] := e - 256; ELSE Tx.Data[idx] := e; END_IF;",
        "idx := idx + 1;",
        "// client id",
        "clen := ClientId.LEN;",
        "Tx.Data[idx] := clen / 256;",
        "idx := idx + 1;",
        "e := clen MOD 256;",
        "IF e > 127 THEN Tx.Data[idx] := e - 256; ELSE Tx.Data[idx] := e; END_IF;",
        "idx := idx + 1;",
        "COP(ClientId.DATA[0], Tx.Data[idx], clen);",
        "idx := idx + clen;",
        "// username",
        "ulen := User.LEN;",
        "Tx.Data[idx] := ulen / 256;",
        "idx := idx + 1;",
        "e := ulen MOD 256;",
        "IF e > 127 THEN Tx.Data[idx] := e - 256; ELSE Tx.Data[idx] := e; END_IF;",
        "idx := idx + 1;",
        "COP(User.DATA[0], Tx.Data[idx], ulen);",
        "idx := idx + ulen;",
        "// password",
        "plen := Pass.LEN;",
        "Tx.Data[idx] := plen / 256;",
        "idx := idx + 1;",
        "e := plen MOD 256;",
        "IF e > 127 THEN Tx.Data[idx] := e - 256; ELSE Tx.Data[idx] := e; END_IF;",
        "idx := idx + 1;",
        "COP(Pass.DATA[0], Tx.Data[idx], plen);",
        "idx := idx + plen;",
        "// remaining length (single byte)",
        "rem := idx - 2;",
        "Tx.Data[1] := rem;",
        "Tx.Len := idx;",
    ],
}

# --- AOI_MqttPublish: assemble an MQTT 3.1.1 PUBLISH (QoS0) packet into a byte buffer
AOI_MQTTPUBLISH = {
    "name": "AOI_MqttPublish",
    "description": "Builds an MQTT 3.1.1 PUBLISH (QoS 0) packet (topic + payload) into Tx.Data/Tx.Len.",
    "parameters": [
        {"name": "Topic", "data_type": "STRING", "usage": "InOut", "required": True},
        {"name": "Payload", "data_type": "STRING_512", "usage": "InOut", "required": True},
        {"name": "Tx", "data_type": "UDT_TxBuffer", "usage": "InOut", "required": True},
    ],
    "local_tags": [
        {"name": "idx", "data_type": "DINT"},
        {"name": "rem", "data_type": "DINT"},
        {"name": "X", "data_type": "DINT"},
        {"name": "e", "data_type": "DINT"},
        {"name": "tlen", "data_type": "DINT"},
        {"name": "plen", "data_type": "DINT"},
    ],
    "st_lines": [
        "// MQTT PUBLISH QoS0 + RETAIN (16#31): retained so a new subscriber sees the latest value",
        "// immediately and it appears in the broker/MQTTX topic tree without waiting for the next publish.",
        "tlen := Topic.LEN;",
        "plen := Payload.LEN;",
        "rem := 2 + tlen + plen;",
        "Tx.Data[0] := 16#31;",
        "idx := 1;",
        "X := rem;",
        "REPEAT",
        "    e := X MOD 128;",
        "    X := X / 128;",
        "    IF X > 0 THEN e := e + 128; END_IF;",
        "    IF e > 127 THEN Tx.Data[idx] := e - 256; ELSE Tx.Data[idx] := e; END_IF;",
        "    idx := idx + 1;",
        "UNTIL X = 0",
        "END_REPEAT;",
        "e := tlen / 256;",
        "Tx.Data[idx] := e;",
        "idx := idx + 1;",
        "e := tlen MOD 256;",
        "IF e > 127 THEN Tx.Data[idx] := e - 256; ELSE Tx.Data[idx] := e; END_IF;",
        "idx := idx + 1;",
        "COP(Topic.DATA[0], Tx.Data[idx], tlen);",
        "idx := idx + tlen;",
        "COP(Payload.DATA[0], Tx.Data[idx], plen);",
        "idx := idx + plen;",
        "Tx.Len := idx;",
    ],
}

# --- AnyProtocol socket transport (PROGRAM scope, NOT an AOI) -----------------------
# Logix forbids MESSAGE tags as AOI local tags (import aborts), so the CIP-socket client
# lives at PROGRAM scope: MESSAGE tags + source/dest tags + a CASE state machine. Create
# (instance 0) -> Open -> Write(CONNECT) -> Read(CONNACK) -> Write(PUBLISH loop) -> Delete.
# "Message to Self": ConnectionPath "1, 0", UNCONNECTED (ConnectedFlag 0, front Ethernet port).
# Socket instance is "1" for Open/Write/Read/Delete (first socket after a fresh run; verify
# crResult live). msgWrite is reused for CONNECT and PUBLISH (both WriteSocket to the instance).
_MSG_TAGS = [
    {"name": "msgCreate", "data_type": "MESSAGE",
     "message": {"service_code": "16#004b", "instance": "0", "req_len": "12",
                 "local_element": "createP", "destination_tag": "crResult"}},
    {"name": "msgOpen", "data_type": "MESSAGE",
     "message": {"service_code": "16#004c", "instance": "1", "req_len": "32",
                 "local_element": "openP"}},
    {"name": "msgWrite", "data_type": "MESSAGE",
     "message": {"service_code": "16#004e", "instance": "1", "req_len": "478",
                 "local_element": "writeP", "destination_tag": "wrResult"}},
    {"name": "msgRead", "data_type": "MESSAGE",
     "message": {"service_code": "16#004d", "instance": "1", "req_len": "8",
                 "local_element": "readP", "destination_tag": "rxResult"}},
    {"name": "msgDelete", "data_type": "MESSAGE",
     "message": {"service_code": "16#004f", "instance": "1", "req_len": "0"}},
]

_SOCKET_ST = [
    "// --- AnyProtocol TCP socket client (ENET-AT002; MSG not allowed in AOIs) ---",
    f"AnyCfg.DestAddr := '{BROKER_ADDR}';",
    "AnyCfg.Timeout := 3000;",
    "CASE sockState OF",
    "0:",
    "    sockErr := 0;",
    "    sockState := 10;",
    "10:",
    "    createP.SockType := 1;",
    "    createP.Addr.Family := 2;",
    "    createP.Addr.Port := 0;",
    "    createP.Addr.Addr := 0;",
    "    MSG(msgCreate);",
    "    sockState := 11;",
    "11:",
    "    IF msgCreate.DN THEN",
    "        sockInst := crResult;",
    "        // MOV the created instance into each later MSG (ENET-AT002 requirement)",
    "        msgOpen.Instance := sockInst;",
    "        msgWrite.Instance := sockInst;",
    "        msgRead.Instance := sockInst;",
    "        msgDelete.Instance := sockInst;",
    "        sockState := 20;",
    "    ELSIF msgCreate.ER THEN sockErr := 1; sockState := 90; END_IF;",
    "20:",
    "    openP.Timeout := AnyCfg.Timeout;",
    "    openP.DestAddr := AnyCfg.DestAddr;",
    "    MSG(msgOpen);",
    "    sockState := 21;",
    "21:",
    "    IF msgOpen.DN THEN sockConnected := 1; sockState := 22;",
    "    ELSIF msgOpen.ER THEN sockErr := 1; sockState := 90; END_IF;",
    "22:",
    "    // send MQTT CONNECT",
    "    writeP.Timeout := AnyCfg.Timeout;",
    "    writeP.ToAddr.Family := 0;",
    "    writeP.ToAddr.Port := 0;",
    "    writeP.ToAddr.Addr := 0;",
    "    writeP.BufLen := ConnectBuf.Len;",
    "    COP(ConnectBuf.Data[0], writeP.Buf[0], ConnectBuf.Len);",
    "    msgWrite.REQ_LEN := 16 + writeP.BufLen;   // Timeout(4)+ToAddr(8)+BufLen(4)+data",
    "    MSG(msgWrite);",
    "    sockState := 23;",
    "23:",
    "    IF msgWrite.DN THEN sockState := 24;",
    "    ELSIF msgWrite.ER THEN sockErr := 1; sockState := 90; END_IF;",
    "24:",
    "    // read CONNACK (tolerate timeout; Mosquitto streams)",
    "    readP.Timeout := 1000;",
    "    readP.BufLen := 4;",
    "    MSG(msgRead);",
    "    sockState := 25;",
    "25:",
    "    IF msgRead.DN OR msgRead.ER THEN sockState := 30; END_IF;",
    "30:",
    "    // connected + authed: publish the latest telemetry each pass",
    "    sockState := 40;",
    "40:",
    "    writeP.Timeout := AnyCfg.Timeout;",
    "    writeP.ToAddr.Family := 0;",
    "    writeP.ToAddr.Port := 0;",
    "    writeP.ToAddr.Addr := 0;",
    "    writeP.BufLen := TxBuf.Len;",
    "    COP(TxBuf.Data[0], writeP.Buf[0], TxBuf.Len);",
    "    msgWrite.REQ_LEN := 16 + writeP.BufLen;   // source length must match 16+BufLen (else 16#0015)",
    "    MSG(msgWrite);",
    "    sockState := 41;",
    "41:",
    "    IF msgWrite.DN THEN sockState := 30;",
    "    ELSIF msgWrite.ER THEN sockErr := 1; sockState := 90; END_IF;",
    "90:",
    "    MSG(msgDelete);",
    "    sockConnected := 0;",
    "    sockInst := 0;",
    "    sockState := 0;",
    "END_CASE;",
]

# --- MainProgram -------------------------------------------------------------------
MAIN_PROGRAM = {
    "name": "MainProgram",
    "main_routine": "MainRoutine",
    "scoped_tags": [
        {"name": "Chiller1", "data_type": "UDT_Chiller"},
        {"name": "SimInst", "data_type": "AOI_ChillerSim"},
        {"name": "JsonInst", "data_type": "AOI_JsonBuild"},
        {"name": "JsonBuf", "data_type": "STRING_512"},
        {"name": "ConnInst", "data_type": "AOI_MqttConnect"},
        {"name": "MqttInst", "data_type": "AOI_MqttPublish"},
        {"name": "TxBuf", "data_type": "UDT_TxBuffer"},
        {"name": "ConnectBuf", "data_type": "UDT_TxBuffer"},
        {"name": "Topic", "data_type": "STRING"},
        {"name": "MqttClientId", "data_type": "STRING"},
        {"name": "MqttUser", "data_type": "STRING"},
        {"name": "MqttPass", "data_type": "STRING"},
        {"name": "MqttKeepAlive", "data_type": "DINT"},
        {"name": "seq", "data_type": "DINT"},
        {"name": "AnyCfg", "data_type": "UDT_AnyCfg"},
        # socket transport (program scope)
        {"name": "sockState", "data_type": "DINT"},
        {"name": "sockInst", "data_type": "DINT"},
        {"name": "sockConnected", "data_type": "BOOL"},
        {"name": "sockErr", "data_type": "BOOL"},
        {"name": "createP", "data_type": "UDT_SockCreate"},
        {"name": "openP", "data_type": "UDT_SockOpen"},
        {"name": "writeP", "data_type": "UDT_SockWrite"},
        {"name": "readP", "data_type": "UDT_SockRead"},
        {"name": "crResult", "data_type": "DINT"},
        {"name": "wrResult", "data_type": "DINT"},
        {"name": "rxResult", "data_type": "UDT_SockRxResult"},
    ] + _MSG_TAGS,
    "routines": [
        {"name": "MainRoutine", "type": "ST", "lines": [
            "seq := seq + 1;",
            "// ---- chiller controls + plant simulation ----",
            "SimInst.Enable := 1;",
            "SimInst.RunCmd := 1;",
            "SimInst.CHW_Setpoint := 44.0;",
            "SimInst.DemandLimit := 100.0;",
            "SimInst.LoadDemand := 75.0;",
            "SimInst.DeltaT := 1.0;",
            "AOI_ChillerSim(SimInst, Chiller1);",
            "// ---- build telemetry JSON (custom grouped structure) ----",
            "JsonInst.Seq := seq;",
            "AOI_JsonBuild(JsonInst, Chiller1, JsonBuf);",
            "// ---- MQTT config (lab; production: load creds from secure store / online write) ----",
            "MqttClientId := 'chiller-CH1';",
            "MqttUser := 'mqttuser';",
            "MqttPass := 'CHANGE_ME';",
            "MqttKeepAlive := 60;",
            "ConnInst.KeepAlive := MqttKeepAlive;",
            "AOI_MqttConnect(ConnInst, MqttClientId, MqttUser, MqttPass, ConnectBuf);",
            "// ---- assemble PUBLISH packet ----",
            f"Topic := '{TOPIC}';",
            "AOI_MqttPublish(MqttInst, Topic, JsonBuf, TxBuf);",
        ] + _SOCKET_ST},
    ],
}


async def _maybe(x):
    return await x if asyncio.iscoroutine(x) else x


async def main():
    tmp = tempfile.mkdtemp(prefix="chiller_")
    base_acd = os.path.join(tmp, "base.ACD")
    base_l5x = os.path.join(tmp, "base.L5X")
    out_l5x = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ChillerSim.L5X")
    try:
        await S.create_project(base_acd, MAJOR_REV, PROCESSOR, CONTROLLER)
    except Exception as e:
        print("create with", PROCESSOR, "failed:", repr(e)[:80], "-> 1756-L85E")
        await S.create_project(base_acd, MAJOR_REV, "1756-L85E", CONTROLLER)
    await S.export_l5x(base_acd, base_l5x)
    await S.close_project(base_acd)
    base = open(base_l5x, encoding="utf-8").read()

    gen = l5xgen.inject(
        base,
        udts=[UDT_CHILLER, STRING_512, UDT_SOCKADDR, UDT_SOCKCREATE, UDT_SOCKOPEN,
              UDT_SOCKWRITE, UDT_SOCKREAD, UDT_SOCKRXRESULT, UDT_TXBUFFER, UDT_ANYCFG],
        aois=[AOI_REALTOSTR, AOI_JSONBUILD, AOI_MQTTCONNECT, AOI_MQTTPUBLISH, AOI_CHILLERSIM],
        programs=[MAIN_PROGRAM],
        schedule=["MainProgram"],
    )
    # MainTask PERIODIC 1000 ms so the publish cadence is ~1 s (one PUBLISH per few scans).
    gen = gen.replace('Type="CONTINUOUS"', 'Type="PERIODIC" Rate="1000.0"', 1)
    with open(out_l5x, "w", encoding="utf-8") as f:
        f.write(gen)
    print("generated L5X:", os.path.getsize(out_l5x), "bytes ->", out_l5x)

    r = await S.validate_l5x(out_l5x, build=True)
    print("BUILD OK:", r.get("build_ok"), "| error:", r.get("build_error"))
    print("counts:", r.get("counts"))


if __name__ == "__main__":
    try:
        asyncio.run(main())
        print("CHILLER (controls + sim + JSON + MQTT + socket) DONE")
    except Exception as e:
        print("ERROR:", repr(e))
        traceback.print_exc()
