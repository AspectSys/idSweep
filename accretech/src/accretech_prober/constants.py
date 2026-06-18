"""Static lookup tables for the Accretech UF series prober.

These dictionaries were previously built inside ``AccretechProber.__init__`` in the
SweepMe! driver. They are constant data, so they live here to keep ``core.py`` focused on
behaviour.
"""

from __future__ import annotations

# Status byte (STB) codes returned via SRQ. Codes 0-63 occur randomly and are ignored.
STB_CODES: dict[int, str] = {
    64: "End of GPIB initial setting",
    65: "End of X/Y-axis movement",
    66: "End of movement to coordinator value",
    67: "Z UP (test start)",
    68: "Z DOWN",
    69: "End of marking",
    70: "First chip (End of wafer loading)",
    71: "End of wafer unloading",
    74: "Out of probing area",
    76: "Format error - Execution condition error - Error",
    77: "End of index size setting",
    78: "End of pass count up",
    79: "End of fail count up",
    80: "Wafer count",
    81: "Wafer end - End of sub die",
    82: "Cassette end",
    84: "Alignment rejected",
    85: "Probing stop by command (without alarm)",
    86: "End of cleaning wafer - End of print data reception",
    87: "Warning",
    88: "Test start (Count not needed)",
    89: "End of needle cleaning",
    90: "Probing stop",
    91: "Probing start",
    92: "End of Z UP/Z DOWN",
    93: "End of hot chuck control command reception",
    94: "End of lot process",
    95: "STOP command reception - Removing the cassette",
    97: "Setting and removing the cassette",
    98: "Normal end of commands - Completion of next block transfer",
    99: "Abnormal end of commands",
    100: "Test complete reception",
    101: "Normal end of em command",
    103: "Normal end of map data downloading",
    104: "Abnormal end of map data downloading",
    105: "Ready to execute needle height setting process",
    107: "Start of binary data uploading",
    108: "End of binary data uploading",
    109: "End of last passed die movement",
    110: "Normal end of inspection",
    111: "Abnormal end of inspection",
    112: "End of wafer sensing",
    113: "End of re-execution of wafer alignment process",
    114: "Normal end of auto needle alignment process",
    115: "Abnormal end of auto needle alignment process",
    116: "End of contact height settling",
    117: "Continuous fail error",
    118: "End of wafer loading",
    119: "Centering - Completion of alarm reset",
    120: "Normal end of start command - Request downloading probing result map data",
    121: "Abnormal end of start command",
    122: "End of 1 pas PMI",
    123: "End if fail mark inspection",
    124: "End of preload",
    125: "Probing stop via GEM host",
    127: "End of all sub dies",
}

# Codes 0-63 happen randomly and are explicitly ignored.
for _code in range(64):
    STB_CODES.setdefault(_code, "Unknown - Ignored status byte")
del _code

PROBER_STATUS_CODES: dict[str, str] = {
    "I": "Waiting for lot process to start",
    "C": "Probe card is being changed",
    "R": "Performing lot process",
    "E": "Error is occurring",
}

CASSETTE_STATUS_CODES: dict[str, str] = {
    "0": "Not Ready (No cassette)",
    "1": "Ready (Before lot process start)",
    "2": "Performing lot process",
    "3": "End of lot process",
    "4": "Cassette for rejects",
}

WAFER_STATUS_CODES: dict[str, str] = {
    "0": "No wafer",
    "1": "Before probing start",
    "2": "End of probing",
    "3": "During probing",
}

ERROR_STATUS_CODES: dict[str, str] = {
    "S": "System error",
    "E": "Error",
    "O": "Operator call",
    "W": "Warning error",
    "I": "Information",
}

# Unrecoverable error codes: when one of these is reported after STB 76 the operation can
# never succeed, so the library always raises instead of offering a retry.
ERROR_CODES: dict[str, str] = {
    # GP-IB I/F TRANSMIT ERROR!!
    "O0651": (
        "When the equipment sends STB code or response data to the tester, an error "
        "occurs in the driver software for the GPIB interface control on the prober side."
    ),
    # GP-IB RECEIVE COMMAND FORMAT INVALID!!
    "O0660": "The format of the received command (used characters or number of bytes) is incorrect.",
    # GP-IB COMMAND EXECUTION ERROR!!
    "O0661": "The prober cannot execute the received command due to its status or timing.",
    # GP-IB COMMUNICATION TIMEOUT ERROR!!
    "O0667": (
        "The period of time before sending a GPIB command from the tester side "
        "exceeds the timeout set in the equipment."
    ),
    # GP-IB I/F RECEIVE ERROR!
    "S0650": (
        "When the equipment receives the command from the tester, an error occurs in the "
        "driver software for the GPIB interface control on the prober side"
    ),
}

# Wafer status value (from the wafer status string) that marks the wafer currently on the
# chuck / during testing.
WAFER_STATUS_DURING_TEST = 3
