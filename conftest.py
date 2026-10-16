import os
import sys

# Make the server modules (l5xgen, telemetry, fatsat) importable from tests/.
sys.path.insert(0, os.path.dirname(__file__))
