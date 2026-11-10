# WipEout Omega Save Editor
# Copyright (c) 2026 Synapz3
# SPDX-License-Identifier: MIT  (see LICENSE)
"""WipEout Omega Collection save editor (for the wipeout_pc port / shadPS4-style savedata).

Compatible with wipeout_pc, the native PC port of WipEout Omega Collection:
https://github.com/abdelmoumendeghbouche/wipeout_pc  (tested with CUSA05670, update 01.07).
A separate project, not part of wipeout_pc.

Edits user/savedata/<user>/CUSA05670/PROFILExx/DATA.BIN in place: the file keeps its size and
layout, only the values you change are rewritten. Every save makes a backup first.

Save layout (worked out from the save and the game's code, see README.md):
  0x000-0x3C3  profile header: u32 counters and float totals
  0x3C4-...    a chain of blocks:  u32 name-hash | char name[0x48] | u32 size | 0x18 bytes runtime
               data (heap pointer, ignored by the game on load) | data[size]
"""

__author__ = "Synapz3"
__license__ = "MIT"
__version__ = "1.0"
import os
import re
import shutil
import struct
import subprocess
import sys
import datetime

if sys.version_info < (3, 7):
    sys.exit("WipEout Omega Save Editor needs Python 3.7 or newer (this is %d.%d). "
             "Get it from https://www.python.org/downloads/" % sys.version_info[:2])
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, simpledialog
except ImportError:
    sys.exit("WipEout Omega Save Editor needs Tkinter, which is missing from this Python.\n"
             "Windows: run the python.org installer again, choose Modify and tick 'tcl/tk and IDLE'.\n"
             "Linux: install the python3-tk package (e.g. sudo apt install python3-tk).")

APP_TITLE = "WipEout Omega Save Editor (by Synapz3)"
HEADER_END = 0x3C4
BLOCK_HDR = 0x68

# ----------------------------------------------------------------------------------------------
# Game knowledge (from the game's own XML: plugins/tracks/Definition.xml, xml/XPRanks.xml,
# plugins/grids/grid_XX.xml)
# ----------------------------------------------------------------------------------------------
TRACKS = {
    "01_Track": "Vineta K", "02_Track": "Metropia", "03_Track": "Moa Therma",
    "04_Track": "Chenghou Project", "05_Track": "Ubermall", "06_Track": "Sebenco Climb",
    "07_Track": "Sol 2", "08_Track": "Anulpha Pass",
    "09_Track": "Vineta K (reverse)", "10_Track": "Metropia (reverse)",
    "11_Track": "Moa Therma (reverse)", "12_Track": "Chenghou Project (reverse)",
    "13_Track": "Ubermall (reverse)", "14_Track": "Sebenco Climb (reverse)",
    "15_Track": "Sol 2 (reverse)", "16_Track": "Anulpha Pass (reverse)",
    "17_Track": "Talon's Junction", "18_Track": "Talon's Junction (reverse)",
    "19_Track": "Amphiseum", "20_Track": "Amphiseum (reverse)",
    "21_Track": "Modesto Heights", "22_Track": "Modesto Heights (reverse)",
    "23_Track": "Tech De Ra", "24_Track": "Tech De Ra (reverse)",
    "25_Track": "Zone 1", "26_Track": "Zone 2", "27_Track": "Zone 3", "28_Track": "Zone 4",
    "altima": "Altima", "arena": "Arena", "bridge": "Bridge",
    "cathedral": "Cathedral", "mall": "Mall", "park": "Park",
    "sol": "Sol", "square": "Square", "subway": "Subway", "tower": "Tower",
}
# Single-race leaderboards per track block: 5 speed classes x top 5 (Venom = entries 0-4 and Flash = 5-9
# confirmed on the game's Race Records screen; the rest follow in the game's class order).
# WipEout 2048 names its classes differently; the game's 2048 speed-lap events call C/B/A/A+ internally
# Flash/Rapier/Phantom/Super Phantom, so the first slot is 2048's Class D.
CLASS_NAMES_HD = ["Venom", "Flash", "Rapier", "Phantom", "Super Phantom"]
CLASS_NAMES_2048 = ["Class D", "Class C", "Class B", "Class A", "Class A+"]
CLASS_LAPS_HD = [3, 4, 4, 5, 5]
CLASS_LAPS_2048 = [3, 3, 4, 5, 6]
SPEED_CLASSES = ["%s / 2048 %s" % (h, c) for h, c in zip(CLASS_NAMES_HD, CLASS_NAMES_2048)]


def class_name(track, idx):
    return (CLASS_NAMES_2048 if track in TRACKS_2048 else CLASS_NAMES_HD)[idx]


def class_laps(track, idx):
    return (CLASS_LAPS_2048 if track in TRACKS_2048 else CLASS_LAPS_HD)[idx]
RECORD_POSITIONS = 5
RECORD_ENTRY = 0x98
ALL_CLASSES = "All speed classes"
TRACKS_2048 = {"altima", "arena", "bridge", "cathedral", "mall", "park", "sol", "square", "subway", "tower"}
ZONE_TRACKS = {"25_Track", "26_Track", "27_Track", "28_Track"}


def track_game(name):
    return "2048" if name in TRACKS_2048 else "HD / Fury"


SHIPS = ["Feisar2048", "Qirex2048", "Piranha2048", "AG_Systems2048", "Auricom2048",
         "Feisar", "Qirex", "Piranha", "AG_Systems", "Triakis", "Goteki", "EGX", "Assegai",
         "Mirage", "Harimau", "Auricom", "Icaras", "Tigron", "VanUber"]
# Ship block: the Statistics screen's "team" source (stats_definition.xml: SourceStatID 0 = TIME PER TEAM,
# shown as a time; 1 = LAPS PER TEAM; 2 = LOYALTY). Loyalty rewards: liveries at 4,000 and 8,000
# (plugins/teams/Definition.xml), trophy at 500 per team.
SHIP_FIELDS = ["Time raced (1/100 s)", "Laps", "Loyalty", "Unknown", "Reserved"]
SHIP_TIME, SHIP_LAPS, SHIP_LOYALTY = 0, 1, 2
LOYALTY_TOP_REWARD = 8000
DEFAULT_MAX_LOYALTY = 10000

# XP needed for ranks 1..50 (XPRanks.xml)
XP_RANKS = [15000, 30000, 50000, 70000, 90000, 110000, 135000, 160000, 185000, 210000,
            240000, 270000, 300000, 330000, 360000, 390000, 420000, 450000, 480000, 510000,
            545000, 580000, 615000, 650000, 685000, 720000, 755000, 790000, 825000, 860000,
            900000, 940000, 980000, 1020000, 1060000, 1100000, 1140000, 1190000, 1240000,
            1290000, 1340000, 1390000, 1440000, 1490000, 1540000, 1590000, 1640000, 1690000,
            1740000, 1790000]
MAX_XP = XP_RANKS[-1]

# Profile header = the game's stats array: file offset 4 + 4 * StatID (the game keeps it at profile+0x1C0;
# laps are incremented at +0x1C8, gold medals at +0x20C, XP read at +0x4DC). Names from the game's own stat
# export list and the Statistics screen (stats_definition.xml StatIDs). Units: times in seconds,
# distances in km. (name, type, unit, note)
U, F = "u32", "f32"
STATS = {
    0: ("Speed pads hit", U, "", ""), 1: ("Weapon pads hit", U, "", ""), 2: ("Laps", U, "", "all modes"),
    3: ("Zones", U, "", ""), 4: ("Time looking at stats", U, "s", ""), 5: ("Time in game", U, "s", ""),
    6: ("Time in single race", U, "s", "Arcade"), 7: ("Time in time trial", U, "s", ""),
    8: ("Time in zone mode", U, "s", ""), 9: ("Stats version", U, "", "leave as is"),
    10: ("Time in tournament", U, "s", ""), 11: ("Time in elimination", U, "s", ""),
    12: ("Unknown", U, "", "not exported by the game"), 13: ("Time in speed lap", U, "s", ""),
    14: ("Update announcement version", U, "", "leave as is"), 15: ("Time in online races", U, "s", ""),
    16: ("Perfect laps", U, "", "jumped by 512 once: may also hold flags"), 17: ("Bronze medals", U, "", ""), 18: ("Silver medals", U, "", ""),
    19: ("Gold medals", U, "", ""), 20: ("Tournament points", U, "", ""),
    21: ("Damage taken from weapons", U, "", ""), 22: ("Damage taken from walls", U, "", ""),
    23: ("Damage taken from ships", U, "", ""), 24: ("Energy absorbed", F, "", ""),
    25: ("Distance travelled", F, "km", ""), 26: ("Autopilot distance", F, "km", ""),
    27: ("Autopilots used", U, "", ""), 28: ("Turbos used", U, "", ""), 29: ("Shields used", U, "", ""),
    30: ("Missiles used", U, "", ""), 31: ("Quakes used", U, "", ""), 32: ("Rockets used", U, "", ""),
    33: ("Bombs used", U, "", ""), 34: ("Mines used", U, "", ""), 35: ("Cannons used", U, "", ""),
    36: ("Plasmas used", U, "", ""), 37: ("Leech beams used", U, "", ""),
    38: ("Unknown", U, "", ""), 39: ("Unknown", U, "", ""),
    40: ("Autopilots absorbed", U, "", ""), 41: ("Turbos absorbed", U, "", ""),
    42: ("Shields absorbed", U, "", ""), 43: ("Missiles absorbed", U, "", ""),
    44: ("Quakes absorbed", U, "", ""), 45: ("Rockets absorbed", U, "", ""), 46: ("Bombs absorbed", U, "", ""),
    47: ("Mines absorbed", U, "", ""), 48: ("Cannons absorbed", U, "", ""), 49: ("Plasmas absorbed", U, "", ""),
    50: ("Leech beams absorbed", U, "", ""), 51: ("Unknown", U, "", ""), 52: ("Unknown", U, "", ""),
    53: ("Times eliminated (deaths)", U, "", ""), 54: ("Time in the air", F, "s", ""),
    55: ("Online laps", U, "", ""), 56: ("Damage taken: missiles", U, "", ""),
    57: ("Damage taken: quakes", U, "", ""), 58: ("Damage taken: rockets", U, "", ""),
    59: ("Damage taken: bombs", U, "", ""), 60: ("Damage taken: mines", U, "", ""),
    61: ("Damage taken: cannons", U, "", ""), 62: ("Damage taken: plasma", U, "", ""),
    63: ("Damage taken: leech beam", U, "", ""), 64: ("Photos taken", U, "", "position inferred"),
    65: ("Unknown", U, "", ""), 66: ("Unknown", U, "", ""), 67: ("Kills", U, "", ""),
    68: ("Unknown", U, "", ""), 69: ("Unknown", U, "", ""), 70: ("Imperfect laps", U, "", ""),
    71: ("Successful barrel rolls", U, "", ""), 72: ("Barrel roll attempts", U, "", ""),
    73: ("Side shifts", U, "", ""), 74: ("Unknown", U, "", ""), 75: ("Unknown", U, "", ""),
    76: ("Unknown", U, "", ""), 77: ("Unknown", U, "", ""),
    78: ("Single races won", U, "", "'Racebox' races"), 79: ("Single races total", U, "", "'Racebox' races"),
    80: ("Time in campaign races", U, "s", ""), 81: ("Time in single races", U, "s", "'Racebox'"),
    82: ("Time in NitroBattle", U, "s", ""), 83: ("Time in Detonator", U, "s", ""),
    84: ("Pickups used", U, "", "all"), 85: ("Pickups absorbed", U, "", "all"),
    86: ("Weapons used", U, "", ""), 87: ("Weapons absorbed", U, "", ""),
    88: ("Items used", U, "", "autopilot/turbo/shield"), 89: ("Items absorbed", U, "", ""),
    90: ("Perfect zones", U, "", ""), 91: ("Imperfect zones", U, "", ""),
    92: ("Venom laps", U, "", ""), 93: ("Flash laps", U, "", ""), 94: ("Rapier laps", U, "", ""),
    95: ("Phantom laps", U, "", ""), 96: ("Zone laps", U, "", ""), 97: ("Detonator laps", U, "", ""),
    98: ("NitroBattle laps", U, "", ""), 106: ("Online races played", U, "", ""),
    107: ("Online races won", U, "", ""), 113: ("Pickups absorbed (copy)", U, "", "always equals stat 85"),
    114: ("Online kills", U, "", ""),
    174: ("Session counter A", U, "", "+1 per game session"), 175: ("Session time counter", U, "", "grows each session; not XP"),
    176: ("Session counter B", U, "", "+1 per session"), 177: ("First-time flag", U, "", "0 -> 1 on first 2048 visit"),
    181: ("Session counter C", U, "", "+1 per session"), 182: ("First-time flag", U, "", "0 -> 1 on first 2048 visit"),
    189: ("Time counter", U, "", "grows while playing"), 191: ("Session counter D", U, "", "+1 per session"),
    192: ("2048 events completed?", U, "", "0 -> 1 after the first 2048 event"),
    193: ("2048 slot (-1 = empty)", U, "", "set to -1 on first 2048 visit"),
    194: ("2048 slot (-1 = empty)", U, "", ""), 195: ("2048 slot (-1 = empty)", U, "", ""),
    196: ("2048 slot (-1 = empty)", U, "", ""), 197: ("2048 slot (-1 = empty)", U, "", ""),
    198: ("2048 value", U, "", "set to 127 on first 2048 visit"),
    205: ("Counter", U, "", "grows slowly while racing"), 206: ("Counter", U, "", "grows slowly while racing"),
    210: ("Weapon pads hit (HD/2048 only?)", U, "", "followed stat 1 until Fury was played"),
    199: ("2048 XP", U, "XP", "rank 50 = 1,790,000"), 215: ("Super Phantom laps", U, "", ""),
}
XP_STAT = 199
XP_OFFSET = 4 + 4 * XP_STAT          # 0x320
U32_MAX = 0xFFFFFFFF


def stat_info(off):
    """(stat id or None, name, type, unit, note) for a header offset."""
    if off == 0:
        return None, "Header (always 0x2000)", U, "", "leave as is"
    sid = (off - 4) // 4
    name, typ, unit, note = STATS.get(sid, ("Unknown", U, "", ""))
    return sid, name, typ, unit, note


def fmt_seconds(s):
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, sec)

SETTING_CHOICES = {"FE_ON": ["FE_ON", "FE_OFF"], "FE_OFF": ["FE_ON", "FE_OFF"]}

# Campaign cell save data (0x48 bytes), as the game's race-result code uses it:
#   +0 u32 best result (position, time in 1/100 s or score; 0 or 0xFFFFFFFF = none)
#   +4 u32 second best (best lap in timed events)
#   +8 u8  difficulty the medal was won on (0 Novice, 1 Skilled, 2 Elite; 0xFF = none)
#   +9 u8  medal (0 gold, 1 silver, 2 bronze, 0xFF = none). Each medal gives 3 - medal campaign points.
MEDALS = {0: "Gold", 1: "Silver", 2: "Bronze", 0xFF: "-"}
MEDAL_IDS = {"Gold": 0, "Silver": 1, "Bronze": 2, "None": 0xFF}
DIFFS = {0: "Novice", 1: "Skilled", 2: "Elite", 0xFF: "-"}
DIFF_IDS = {"Novice": 0, "Skilled": 1, "Elite": 2}
TIMED_MODES = {"Time Trial", "Speed Lap"}

# cell: (campaign, track, mode, laps, lower_is_better, elite_gold_target), from plugins/grids/grid_XX.xml
CELLS = {
    'grid0_3_1': ('HD', '01_Track', 'Race', 3, True, 1),
    'grid0_5_1': ('HD', '08_Track', 'Race', 3, True, 1),
    'grid0_2_2': ('HD', '08_Track', 'Time Trial', 3, True, 11100),
    'grid0_4_2': ('HD', '01_Track', 'Zone', 0, False, 20),
    'grid0_1_2': ('HD', '01_Track', 'Speed Lap', 7, True, 2950),
    'grid0_3_2': ('HD', '08_Track', 'Speed Lap', 7, True, 3700),
    'grid1_2_1': ('HD', '04_Track', 'Race', 4, True, 1),
    'grid1_4_1': ('HD', '08_Track', 'Race', 4, True, 1),
    'grid1_3_1': ('HD', '03_Track', 'Race', 3, True, 1),
    'grid1_2_2': ('HD', '08_Track', 'Zone', 0, False, 25),
    'grid1_4_2': ('HD', '01_Track', 'Time Trial', 3, True, 9750),
    'grid1_3_2': ('HD', '04_Track', 'Speed Lap', 7, True, 3850),
    'grid1_2_3': ('HD', '03_Track', 'Time Trial', 3, True, 12000),
    'grid1_4_3': ('HD', '03_Track', 'Speed Lap', 7, True, 3600),
    'grid2_4_1': ('HD', '04_Track', 'Time Trial', 4, True, 13600),
    'grid2_1_1': ('HD', '04_Track', 'Speed Lap', 7, True, 3025),
    'grid2_3_1': ('HD', '02_Track', 'Race', 3, True, 1),
    'grid2_5_1': ('HD', '02_Track', 'Time Trial', 4, True, 12200),
    'grid2_2_2': ('HD', '03_Track', 'Zone', 0, False, 30),
    'grid2_4_2': ('HD', None, 'Tournament', 3, True, 1),
    'grid2_1_2': ('HD', '03_Track', 'Race', 4, True, 1),
    'grid2_3_2': ('HD', '06_Track', 'Time Trial', 3, True, 11300),
    'grid2_5_2': ('HD', '06_Track', 'Race', 4, True, 1),
    'grid2_4_3': ('HD', '02_Track', 'Speed Lap', 7, True, 3150),
    'grid3_2_1': ('HD', '02_Track', 'Speed Lap', 7, True, 3350),
    'grid3_1_1': ('HD', None, 'Tournament', 4, True, 1),
    'grid3_3_1': ('HD', '05_Track', 'Race', 3, True, 1),
    'grid3_5_1': ('HD', '05_Track', 'Speed Lap', 7, True, 2975),
    'grid3_2_2': ('HD', '03_Track', 'Race', 4, True, 1),
    'grid3_4_2': ('HD', '04_Track', 'Zone', 0, False, 22),
    'grid3_1_2': ('HD', '01_Track', 'Time Trial', 4, True, 9650),
    'grid3_3_2': ('HD', '06_Track', 'Speed Lap', 7, True, 3225),
    'grid3_5_2': ('HD', '06_Track', 'Race', 4, True, 1),
    'grid3_4_3': ('HD', '05_Track', 'Time Trial', 4, True, 12100),
    'grid4_1_1': ('HD', '05_Track', 'Time Trial', 4, True, 10500),
    'grid4_3_1': ('HD', '07_Track', 'Race', 3, True, 1),
    'grid4_5_1': ('HD', '04_Track', 'Time Trial', 4, True, 11800),
    'grid4_2_2': ('HD', '07_Track', 'Speed Lap', 7, True, 2850),
    'grid4_4_2': ('HD', '02_Track', 'Zone', 0, False, 25),
    'grid4_3_2': ('HD', '08_Track', 'Time Trial', 4, True, 12300),
    'grid4_2_3': ('HD', '01_Track', 'Speed Lap', 7, True, 3000),
    'grid4_4_3': ('HD', '07_Track', 'Race', 4, True, 1),
    'grid4_1_3': ('HD', None, 'Tournament', 3, True, 1),
    'grid4_5_3': ('HD', '05_Track', 'Speed Lap', 7, True, 2600),
    'grid5_4_1': ('HD', None, 'Tournament', 4, True, 1),
    'grid5_1_1': ('HD', '03_Track', 'Time Trial', 4, True, 13800),
    'grid5_3_1': ('HD', '02_Track', 'Race', 4, True, 1),
    'grid5_5_1': ('HD', '02_Track', 'Race', 4, True, 1),
    'grid5_0_2': ('HD', '06_Track', 'Speed Lap', 7, True, 2850),
    'grid5_2_2': ('HD', '06_Track', 'Zone', 0, False, 25),
    'grid5_4_2': ('HD', '01_Track', 'Time Trial', 4, True, 11000),
    'grid5_6_2': ('HD', '07_Track', 'Time Trial', 4, True, 9850),
    'grid5_1_2': ('HD', '07_Track', 'Race', 4, True, 1),
    'grid5_3_2': ('HD', '05_Track', 'Speed Lap', 7, True, 3300),
    'grid5_5_2': ('HD', '08_Track', 'Speed Lap', 7, True, 2575),
    'grid5_2_3': ('HD', '04_Track', 'Race', 3, True, 1),
    'grid6_1_0': ('HD', '07_Track', 'Speed Lap', 7, True, 2450),
    'grid6_5_0': ('HD', '02_Track', 'Time Trial', 4, True, 10400),
    'grid6_1_1': ('HD', '08_Track', 'Time Trial', 4, True, 10450),
    'grid6_3_1': ('HD', '06_Track', 'Race', 3, True, 1),
    'grid6_5_1': ('HD', None, 'Tournament', 4, True, 1),
    'grid6_0_2': ('HD', '04_Track', 'Race', 4, True, 1),
    'grid6_2_2': ('HD', '07_Track', 'Zone', 0, False, 25),
    'grid6_4_2': ('HD', '05_Track', 'Zone', 0, False, 25),
    'grid6_6_2': ('HD', '03_Track', 'Speed Lap', 7, True, 2875),
    'grid6_1_2': ('HD', None, 'Tournament', 4, True, 1),
    'grid6_3_2': ('HD', '04_Track', 'Speed Lap', 7, True, 3250),
    'grid6_5_2': ('HD', '07_Track', 'Time Trial', 3, True, 9775),
    'grid6_2_3': ('HD', '05_Track', 'Race', 4, True, 1),
    'grid6_4_3': ('HD', '06_Track', 'Time Trial', 4, True, 12800),
    'grid7_0_1': ('HD', '03_Track', 'Time Trial', 5, True, 12700),
    'grid7_6_1': ('HD', '02_Track', 'Race', 5, True, 1),
    'grid7_1_1': ('HD', '06_Track', 'Race', 5, True, 1),
    'grid7_3_1': ('HD', '04_Track', 'Speed Lap', 7, True, 2550),
    'grid7_5_1': ('HD', '05_Track', 'Race', 5, True, 1),
    'grid7_0_2': ('HD', '01_Track', 'Race', 5, True, 1),
    'grid7_2_2': ('HD', '02_Track', 'Time Trial', 5, True, 11500),
    'grid7_4_2': ('HD', '03_Track', 'Race', 5, True, 1),
    'grid7_6_2': ('HD', '07_Track', 'Speed Lap', 7, True, 2150),
    'grid7_1_2': ('HD', '08_Track', 'Speed Lap', 7, True, 2250),
    'grid7_3_2': ('HD', None, 'Tournament', 5, True, 1),
    'grid7_5_2': ('HD', '01_Track', 'Time Trial', 5, True, 10400),
    'grid7_0_3': ('HD', '04_Track', 'Race', 5, True, 1),
    'grid7_2_3': ('HD', '07_Track', 'Race', 5, True, 1),
    'grid7_4_3': ('HD', '06_Track', 'Speed Lap', 7, True, 2550),
    'grid7_6_3': ('HD', '08_Track', 'Race', 5, True, 1),
    'grid7_3_3': ('HD', '05_Track', 'Time Trial', 5, True, 11700),
    'grid8_2_1': ('Fury', '19_Track', 'NitroBattle', 0, True, 1),
    'grid8_4_1': ('Fury', '21_Track', 'Race', 3, True, 1),
    'grid8_3_1': ('Fury', '17_Track', 'Race', 3, True, 1),
    'grid8_2_2': ('Fury', '17_Track', 'Speed Lap', 7, True, 3400),
    'grid8_4_2': ('Fury', '19_Track', 'Speed Lap', 7, True, 4350),
    'grid8_3_2': ('Fury', '19_Track', 'Elimination', 0, True, 1),
    'grid8_4_3': ('Fury', '26_Track', 'Detonator', 0, False, 340000),
    'grid9_4_1': ('Fury', '25_Track', 'Zone', 0, False, 20),
    'grid9_3_1': ('Fury', '23_Track', 'Race', 3, True, 1),
    'grid9_2_2': ('Fury', '23_Track', 'Elimination', 0, True, 1),
    'grid9_4_2': ('Fury', '17_Track', 'Elimination', 0, True, 1),
    'grid9_1_2': ('Fury', '18_Track', 'Speed Lap', 7, True, 3700),
    'grid9_3_2': ('Fury', '18_Track', 'Detonator', 0, False, 320000),
    'grid9_5_2': ('Fury', '25_Track', 'NitroBattle', 0, True, 1),
    'grid10_2_1': ('Fury', '19_Track', 'Race', 3, True, 1),
    'grid10_3_1': ('Fury', '22_Track', 'Elimination', 0, True, 1),
    'grid10_5_1': ('Fury', None, 'Tournament', 3, True, 1),
    'grid10_2_2': ('Fury', '24_Track', 'NitroBattle', 0, True, 1),
    'grid10_4_2': ('Fury', '23_Track', 'Speed Lap', 7, True, 3600),
    'grid10_1_2': ('Fury', '25_Track', 'Detonator', 0, False, 360000),
    'grid10_3_2': ('Fury', '19_Track', 'Detonator', 0, False, 340000),
    'grid10_5_2': ('Fury', '22_Track', 'Race', 4, True, 1),
    'grid10_4_3': ('Fury', '18_Track', 'Elimination', 0, True, 1),
    'grid11_2_1': ('Fury', '24_Track', 'Elimination', 0, True, 1),
    'grid11_4_1': ('Fury', '27_Track', 'NitroBattle', 0, True, 1),
    'grid11_3_1': ('Fury', '24_Track', 'Race', 4, True, 1),
    'grid11_5_1': ('Fury', '22_Track', 'Elimination', 0, True, 1),
    'grid11_2_2': ('Fury', '20_Track', 'Detonator', 0, False, 350000),
    'grid11_4_2': ('Fury', '18_Track', 'Race', 4, True, 1),
    'grid11_1_2': ('Fury', '26_Track', 'Zone', 0, False, 25),
    'grid11_3_2': ('Fury', '22_Track', 'Speed Lap', 7, True, 3200),
    'grid11_2_3': ('Fury', '26_Track', 'NitroBattle', 0, True, 1),
    'grid12_2_1': ('Fury', '18_Track', 'Elimination', 0, True, 1),
    'grid12_4_1': ('Fury', '21_Track', 'Elimination', 0, True, 1),
    'grid12_3_1': ('Fury', '17_Track', 'Race', 4, True, 1),
    'grid12_5_1': ('Fury', '23_Track', 'Detonator', 0, False, 280000),
    'grid12_2_2': ('Fury', '21_Track', 'Speed Lap', 7, True, 2800),
    'grid12_4_2': ('Fury', '20_Track', 'NitroBattle', 0, True, 1),
    'grid12_1_2': ('Fury', '22_Track', 'NitroBattle', 0, True, 1),
    'grid12_3_2': ('Fury', '22_Track', 'Detonator', 0, False, 320000),
    'grid12_5_2': ('Fury', None, 'Tournament', 4, True, 1),
    'grid12_2_3': ('Fury', '20_Track', 'Elimination', 0, True, 1),
    'grid12_4_3': ('Fury', '19_Track', 'Race', 4, True, 1),
    'grid13_5_0': ('Fury', '28_Track', 'NitroBattle', 0, True, 1),
    'grid13_2_1': ('Fury', '20_Track', 'Elimination', 0, True, 1),
    'grid13_1_1': ('Fury', None, 'Tournament', 4, True, 1),
    'grid13_3_1': ('Fury', '20_Track', 'Race', 4, True, 1),
    'grid13_5_1': ('Fury', '17_Track', 'Elimination', 0, True, 1),
    'grid13_2_2': ('Fury', '23_Track', 'NitroBattle', 0, True, 1),
    'grid13_4_2': ('Fury', '21_Track', 'Detonator', 0, False, 250000),
    'grid13_1_2': ('Fury', '27_Track', 'Zone', 0, False, 30),
    'grid13_3_2': ('Fury', '24_Track', 'Speed Lap', 7, True, 2800),
    'grid13_5_2': ('Fury', '22_Track', 'Speed Lap', 7, True, 2600),
    'grid13_4_3': ('Fury', '19_Track', 'Elimination', 0, True, 1),
    'grid14_2_0': ('Fury', '23_Track', 'Elimination', 0, True, 1),
    'grid14_3_0': ('Fury', '19_Track', 'Speed Lap', 7, True, 3200),
    'grid14_5_0': ('Fury', '17_Track', 'Detonator', 0, False, 340000),
    'grid14_2_1': ('Fury', None, 'Tournament', 4, True, 1),
    'grid14_4_1': ('Fury', '18_Track', 'NitroBattle', 0, True, 1),
    'grid14_3_1': ('Fury', '27_Track', 'Detonator', 0, False, 175000),
    'grid14_2_2': ('Fury', '17_Track', 'Speed Lap', 7, True, 2800),
    'grid14_4_2': ('Fury', '20_Track', 'Speed Lap', 7, True, 3600),
    'grid14_1_2': ('Fury', '21_Track', 'Elimination', 0, True, 1),
    'grid14_3_2': ('Fury', '24_Track', 'Elimination', 0, True, 1),
    'grid14_4_3': ('Fury', '23_Track', 'Race', 4, True, 1),
    'grid14_3_3': ('Fury', '21_Track', 'Race', 4, True, 1),
    'grid14_4_4': ('Fury', '21_Track', 'NitroBattle', 0, True, 1),
    'grid15_4_0': ('Fury', '20_Track', 'Speed Lap', 7, True, 2800),
    'grid15_3_0': ('Fury', '24_Track', 'Detonator', 0, False, 470000),
    'grid15_4_1': ('Fury', '24_Track', 'Race', 5, True, 1),
    'grid15_1_1': ('Fury', '22_Track', 'Race', 5, True, 1),
    'grid15_3_1': ('Fury', '18_Track', 'Race', 5, True, 1),
    'grid15_2_2': ('Fury', '26_Track', 'NitroBattle', 0, True, 1),
    'grid15_4_2': ('Fury', '19_Track', 'Elimination', 0, True, 1),
    'grid15_3_2': ('Fury', '28_Track', 'Zone', 0, False, 55),
    'grid15_5_2': ('Fury', '17_Track', 'NitroBattle', 0, True, 1),
    'grid15_4_3': ('Fury', '20_Track', 'Race', 5, True, 1),
    'grid15_3_3': ('Fury', '23_Track', 'Elimination', 0, True, 1),
    'grid15_5_3': ('Fury', '28_Track', 'Detonator', 0, False, 540000),
    'grid15_2_4': ('Fury', None, 'Tournament', 5, True, 1),
}


# WipEout 2048 event record fields (Mjolnir block), as the game wrote them in play:
#   STATE 0 locked, 2 unlocked, 4 played (the next event then goes 0 -> 2)
#   DONE  u8 1 once finished;  RESULT 0 none, 1 pass, 2 elite (shown in game as "ELITE PASS")
# Best result: race-type records (type 0xE5) keep it in tag 0x4D8: 10 lap slots (u32 value, u16 time in
# 1/100 s, u16), XP earned at +0x98, livery at +0x9C (32 chars), ship at +0xBC (32 chars).
# Time-type records (0xF0: time trials, speed laps) keep it in tag 0x5C0: XP earned at +0, livery at +4,
# ship at +0x24, then (u32 value, u32 time in 1/100 s) at +0x44.
EV_STATE, EV_RESULT, EV_DONE = 0x3C0, 0x3E8, 0x3F0
EV_BEST_RACE, EV_BEST_TIME = 0x4D8, 0x5C0
EV_PLAYED = 4
LIVERIES_2048 = ["speed", "agility", "combat", "prototype"]
SHIPS_2048 = ["Feisar2048", "Qirex2048", "Piranha2048", "AG_Systems2048", "Auricom2048"]

# 2048 campaign events (SP.xml instances): instance id -> (season, name)
EVENTS_2048 = {
    0x7B8172C1: ('2048', '2048 - Event 1'),
    0x0C1FA031: ('2048', '2048 - Event 2'),
    0x846E407C: ('2048', '2048 - Event 2-1'),
    0xDF9C2C75: ('2048', '2048 - Event 2-2'),
    0xBBD652A7: ('2048', '2048 - Event 3'),
    0x350D1FF3: ('2048', '2048 - Event 3-1'),
    0x68597D65: ('2048', '2048 - Event 3-2'),
    0xE32205D1: ('2048', '2048 - Event 4'),
    0x7CAC0BCA: ('2048', '2048 - Event 4-1'),
    0x4AB4A377: ('2048', '2048 - Event 4-2'),
    0x788749BE: ('2048', '2048 - Event 5'),
    0x0EC01CF3: ('2048', '2048 - Event 5-1'),
    0x97227AF2: ('2048', '2048 - Event 5-2'),
    0xBAD0C2F1: ('2048', '2048 - Event 6'),
    0x94BCD721: ('2048', '2048 - Event 7'),
    0x6CA91601: ('2049', '2049 - Event 1'),
    0x1B37C4F1: ('2049', '2049 - Event 2'),
    0xE8C0F07F: ('2049', '2049 - Event 2-1'),
    0x72D5F7AB: ('2049', '2049 - Event 2-2'),
    0xE970BBC4: ('2049', '2049 - Event 2-3'),
    0x0BC33567: ('2049', '2049 - Event 3'),
    0x355629FA: ('2049', '2049 - Event 3-1'),
    0xC3B6A824: ('2049', '2049 - Event 3-2'),
    0x6D3A903B: ('2049', '2049 - Event 3-3'),
    0xF40A6111: ('2049', '2049 - Event 4'),
    0x6FAF2D7E: ('2049', '2049 - Event 5'),
    0xCA2D2E6D: ('2049', '2049 - Event 5-1'),
    0x17185D6B: ('2049', '2049 - Event 5-2'),
    0xA0D1AFFD: ('2049', '2049 - Event 5-3'),
    0x345D4177: ('2049', '2049 - Event 6'),
    0x77E742A3: ('2049', '2049 - Event 6-1'),
    0xB19BFCE8: ('2049', '2049 - Event 6-2'),
    0x2F8BFB62: ('2049', '2049 - Event 6-3'),
    0xE4849EAF: ('2049', '2049 - Event 7'),
    0xF1002C90: ('2049', '2049 - Event 8'),
    0x59B1BE15: ('2050', '2050 - Event 1'),
    0x2E2F6CE5: ('2050', '2050 - Event 2'),
    0xDF845235: ('2050', '2050 - Event 2-1'),
    0x345382E1: ('2050', '2050 - Event 2-2'),
    0x33BFCCAA: ('2050', '2050 - Event 2-3'),
    0xDCE648A2: ('2050', '2050 - Event 2-4'),
    0xF95D0305: ('2050', '2050 - Event 3'),
    0x428BB343: ('2050', '2050 - Event 3-1'),
    0xB65B832E: ('2050', '2050 - Event 3-2'),
    0x284B84A4: ('2050', '2050 - Event 3-3'),
    0xF6447AAA: ('2050', '2050 - Event 3-4'),
    0xC112C905: ('2050', '2050 - Event 4'),
    0x5AB7856A: ('2050', '2050 - Event 5'),
    0xBA49F8F5: ('2050', '2050 - Event 5-1'),
    0x492084B9: ('2050', '2050 - Event 5-2'),
    0x5672666A: ('2050', '2050 - Event 5-3'),
    0x0E86311C: ('2050', '2050 - Event 5-4'),
    0x0145E963: ('2050', '2050 - Event 6'),
    0xEA4463D7: ('2050', '2050 - Event 6-1'),
    0xF4EAE877: ('2050', '2050 - Event 6-2'),
    0x6AFAEFFD: ('2050', '2050 - Event 6-3'),
    0x72E714C7: ('2050', '2050 - Event 6-4'),
    0xB68C1BF5: ('2050', '2050 - Event 7'),
    0xDA7C8441: ('2050', '2050 - Event 8'),
    0x5FBDC8EB: ('2050', '2050 - Event 9'),
    0xF3179198: ('Challenge', 'AG-Systems P Ship Challenge'),
    0x8CFCF1C3: ('Challenge', 'Altima - S Phantom Challenge'),
    0xB2089A65: ('Challenge', 'Arena - S Phantom Challenge'),
    0x1C1FE05F: ('Challenge', 'Auricom P Ship Challenge'),
    0x57DCA54E: ('Challenge', 'Bridge - S Phantom Challenge'),
    0x94B8DE76: ('Challenge', 'Cathedral - S Phantom Challenge'),
    0x2E51FA10: ('Challenge', 'Feisar - P Ship Challenge'),
    0x43D58E18: ('Challenge', 'Mall - S Phantom Challenge'),
    0xA50B16C4: ('Challenge', 'Park - S Phantom Challenge'),
    0xF3C27500: ('Challenge', 'Pirhana P Ship Challenge'),
    0xB02B98B4: ('Challenge', 'Qirex P Ship Challenge'),
    0xEEC6EA15: ('Challenge', 'Sol - S Phantom Challenge'),
    0x121C2AC9: ('Challenge', 'Square - S Phantom Challenge'),
    0xBBD6DF98: ('Challenge', 'Subway - S Phantom Challenge'),
    0x98CC6110: ('Challenge', 'Tower - S Phantom Challenge'),
    0xFE528A0A: ('Speed Lap', 'Altima Speed Lap - Flash'),
    0xE5F07608: ('Speed Lap', 'Altima Speed Lap - Phantom'),
    0x9C3CC920: ('Speed Lap', 'Altima Speed Lap - Rapier'),
    0x749BA036: ('Speed Lap', 'Altima Speed Lap - Super P'),
    0xA8C06DCC: ('Speed Lap', 'Arena Speed Lap - Flash'),
    0x463E2789: ('Speed Lap', 'Arena Speed Lap - Phantom'),
    0xEE6D3C42: ('Speed Lap', 'Arena Speed Lap - Rapier'),
    0xD755F1B7: ('Speed Lap', 'Arena Speed Lap - Super P'),
    0xD478A57F: ('Speed Lap', 'Bridge Speed Lap - Flash'),
    0x1C69ECC8: ('Speed Lap', 'Bridge Speed Lap - Phantom'),
    0xBC7966BC: ('Speed Lap', 'Bridge Speed Lap - Rapier'),
    0x8D023AF6: ('Speed Lap', 'Bridge Speed Lap - Super P'),
    0x6B8C0347: ('Speed Lap', 'Cathedral Speed Lap - Flash'),
    0x3443E91A: ('Speed Lap', 'Cathedral Speed Lap - Phantom'),
    0x94C42A84: ('Speed Lap', 'Cathedral Speed Lap - Rapier'),
    0xA5283F24: ('Speed Lap', 'Cathedral Speed Lap - Super P'),
    0xE553C7E7: ('Speed Lap', 'Mall Speed Lap - Flash'),
    0x064DDD85: ('Speed Lap', 'Mall Speed Lap - Phantom'),
    0x429C56A8: ('Speed Lap', 'Mall Speed Lap - Rapier'),
    0x97260BBB: ('Speed Lap', 'Mall Speed Lap - Super P'),
    0xB77686F5: ('Speed Lap', 'Park Speed Lap - Flash'),
    0x7F628E75: ('Speed Lap', 'Park Speed Lap - Phantom'),
    0xB17702A1: ('Speed Lap', 'Park Speed Lap - Rapier'),
    0xEE09584B: ('Speed Lap', 'Park Speed Lap - Super P'),
    0xDA307F36: ('Speed Lap', 'Sol Speed Lap - Flash'),
    0x5BD42D06: ('Speed Lap', 'Sol Speed Lap - Phantom'),
    0xB377D752: ('Speed Lap', 'Sol Speed Lap - Rapier'),
    0xCABFFB38: ('Speed Lap', 'Sol Speed Lap - Super P'),
    0x0C281D04: ('Speed Lap', 'Square Speed Lap - Flash'),
    0x1518AF1B: ('Speed Lap', 'Square Speed Lap - Phantom'),
    0x7B769EB0: ('Speed Lap', 'Square Speed Lap - Rapier'),
    0x84737925: ('Speed Lap', 'Square Speed Lap - Super P'),
    0xD76ECB21: ('Speed Lap', 'Subway Speed Lap - Flash'),
    0x925BD35B: ('Speed Lap', 'Subway Speed Lap - Phantom'),
    0x30A90C21: ('Speed Lap', 'Subway Speed Lap - Rapier'),
    0x03300565: ('Speed Lap', 'Subway Speed Lap - Super P'),
    0x162D35C8: ('Speed Lap', 'Tower Speed Lap - Flash'),
    0x47E285A6: ('Speed Lap', 'Tower Speed Lap - Phantom'),
    0xE9BE1503: ('Speed Lap', 'Tower Speed Lap - Rapier'),
    0xD6895398: ('Speed Lap', 'Tower Speed Lap - Super P'),
}


# laps per 2048 event (SP.xml m_numOfLaps; 0 = not lap based)
EVENT_LAPS = {0xDF9C2C75: 2, 0xBBD652A7: 2, 0x350D1FF3: 2, 0xC3B6A824: 2, 0xA0D1AFFD: 3, 0x345D4177: 3, 0xDF845235: 3, 0x33BFCCAA: 2, 0x428BB343: 3, 0xBA49F8F5: 3, 0x5672666A: 3, 0x0145E963: 3, 0x73DE3715: 3, 0x1EABE68F: 3, 0xA9D9DF33: 3, 0x780FDBB7: 3, 0xE3AA97D8: 3, 0xE9A1DE6F: 3, 0xC6AC9206: 3, 0x8075A7BD: 3, 0x59CBB91B: 3, 0x04A115C2: 3, 0x5DB1DA32: 3, 0xFD1DB100: 3, 0x359A1819: 3, 0x3364AA26: 3, 0x9012240E: 3, 0xF3179198: 10, 0xF3C27500: 2, 0x2E51FA10: 10, 0x7B8172C1: 3, 0x0C1FA031: 3, 0x846E407C: 3, 0xE32205D1: 3, 0x7CAC0BCA: 3, 0x788749BE: 3, 0x94BCD721: 5, 0x6CA91601: 3, 0x1B37C4F1: 3, 0x72D5F7AB: 1, 0xF40A6111: 3, 0x6FAF2D7E: 1, 0x17185D6B: 3, 0xF1002C90: 5, 0x59B1BE15: 3, 0x2E2F6CE5: 3, 0xF6447AAA: 3, 0x0E86311C: 3, 0xEA4463D7: 1, 0x72E714C7: 3, 0xE970BBC4: 3, 0x121C2AC9: 10, 0xA50B16C4: 10, 0x94B8DE76: 10, 0xBBD6DF98: 10, 0xEEC6EA15: 10, 0x8CFCF1C3: 10, 0x57DCA54E: 10, 0xB68C1BF5: 3, 0x5AB7856A: 3, 0xC112C905: 3, 0x5FBDC8EB: 5, 0x43D58E18: 10, 0x98CC6110: 10, 0xB2089A65: 10}


def rank_for_xp(xp):
    return sum(1 for t in XP_RANKS if xp >= t)


def fmt_time(cs):
    if cs is None or cs in (0xFFFF, 0xFFFFFFFF):
        return "--"
    m, rem = divmod(cs, 6000)
    return "%d:%02d.%02d" % (m, rem // 100, rem % 100)


def parse_time(text):
    """'1:23.45', '83.45', '00:00.01' or '8345cs' -> centiseconds."""
    text = text.strip()
    if text.endswith("cs"):
        return int(text[:-2])
    m = re.fullmatch(r"(?:(\d+):)?(\d+)(?:\.(\d{1,2}))?", text)
    if not m:
        raise ValueError("Use m:ss.cc, for example 1:05.32")
    minutes = int(m.group(1) or 0)
    secs = int(m.group(2))
    cs = int((m.group(3) or "0").ljust(2, "0"))
    if m.group(1) is not None and secs >= 60:
        raise ValueError("Seconds must be below 60")
    return (minutes * 60 + secs) * 100 + cs


def ordinal(n):
    return "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


# ----------------------------------------------------------------------------------------------
# Save model (no GUI code here, so it can be tested on its own)
# ----------------------------------------------------------------------------------------------
class Block:
    __slots__ = ("hdr", "name", "size", "data")

    def __init__(self, hdr, name, size, data):
        self.hdr, self.name, self.size, self.data = hdr, name, size, data


class SaveFile:
    FMT = {"u8": "<B", "u16": "<H", "u32": "<I", "i32": "<i", "f32": "<f"}

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.buf = bytearray(f.read())
        self.original = bytes(self.buf)
        self.blocks = self._parse()
        self.by_name = {}
        for b in self.blocks:
            self.by_name.setdefault(b.name, b)

    def _parse(self):
        d = self.buf
        if len(d) < HEADER_END + BLOCK_HDR:
            raise ValueError("File is too small to be a WipEout Omega profile.")
        out, h = [], HEADER_END
        while h + BLOCK_HDR <= len(d):
            name = bytes(d[h + 4:h + 0x4C]).split(b"\0")[0].decode("latin1")
            size = struct.unpack_from("<I", d, h + 0x4C)[0]
            if h + BLOCK_HDR + size > len(d) or not name:
                raise ValueError("Unexpected data at 0x%X: not a WipEout Omega profile?" % h)
            out.append(Block(h, name, size, h + BLOCK_HDR))
            h += BLOCK_HDR + size
        if h != len(d) or not out:
            raise ValueError("Block chain does not end at the end of the file.")
        return out

    @property
    def dirty(self):
        return bytes(self.buf) != self.original

    # -- typed access -------------------------------------------------------------------------
    def get(self, off, typ="u32"):
        return struct.unpack_from(self.FMT[typ], self.buf, off)[0]

    def set(self, off, typ, value):
        struct.pack_into(self.FMT[typ], self.buf, off, value)

    def get_str(self, off, n):
        return bytes(self.buf[off:off + n]).split(b"\0")[0].decode("latin1")

    def set_str(self, off, n, text):
        raw = text.encode("latin1")
        if len(raw) >= n:
            raise ValueError("Text is too long (max %d characters)" % (n - 1))
        self.buf[off:off + n] = raw + b"\0" * (n - len(raw))

    # -- header ---------------------------------------------------------------------------------
    def header_fields(self):
        return [(o, self.get(o)) for o in range(0, HEADER_END, 4)]

    # -- ships -------------------------------------------------------------------------------
    def ships(self):
        res = []
        for name in SHIPS:
            b = self.by_name.get(name)
            if b and b.size == 0x14:
                res.append((name, b, [self.get(b.data + 4 * i) for i in range(5)]))
        return res

    # -- track records -----------------------------------------------------------------------
    def track_blocks(self):
        return [b for b in self.blocks if b.name in TRACKS and b.size == 0x4000]

    @staticmethod
    def entry_off(b, cls=0, pos=0):
        """Single-race leaderboard entry: block = u32 laps driven on the track, then 5 speed classes x
        top 5 entries of 0x98 bytes (10 laps of u32 speed pads + u16 time + u16, team at +0x50,
        pilot at +0x70). Confirmed against the game's Race Records screen."""
        return b.data + 4 + (cls * RECORD_POSITIONS + pos) * RECORD_ENTRY

    def track_record(self, b, cls=0, pos=0):
        base = self.entry_off(b, cls, pos)
        laps = []
        for i in range(10):
            o = base + 8 * i
            laps.append({"slot": i, "off": o, "stat": self.get(o), "time": self.get(o + 4, "u16")})
        valid = [l for l in laps if l["time"] != 0xFFFF]
        has = bool(valid)
        return {"laps": laps, "valid": valid,
                "ship": self.get_str(base + 0x50, 0x20) if has else "",
                "pilot": self.get_str(base + 0x70, 8) if has else "",
                "best": min((l["time"] for l in valid), default=None),
                "total": sum(l["time"] for l in valid) if valid else None,
                "pads": sum(l["stat"] for l in valid) if valid else None}

    def class_entries(self, b, cls):
        return sum(1 for p in range(RECORD_POSITIONS) if self.track_record(b, cls, p)["valid"])

    def any_record(self, b):
        return any(self.track_record(b, c, 0)["valid"] for c in range(len(SPEED_CLASSES)))

    def _copy_entry(self, b, cls, src, dst):
        s, d = self.entry_off(b, cls, src), self.entry_off(b, cls, dst)
        self.buf[d:d + RECORD_ENTRY] = self.buf[s:s + RECORD_ENTRY]

    def _clear_entry(self, b, cls, pos):
        base = self.entry_off(b, cls, pos)
        for i in range(10):
            self.set(base + 8 * i, "u32", 0)
            self.set(base + 8 * i + 4, "u16", 0xFFFF)
            self.set(base + 8 * i + 6, "u16", 0)
        self.set_str(base + 0x50, 0x20, "")
        self.set_str(base + 0x70, 8, "")

    def write_record(self, b, laps, ship, pilot, cls=0, pos=0, push=False):
        """laps: list of (speed pads, centiseconds); an empty list removes the entry and moves the
        slower entries up. push=True inserts the entry and moves the others down (the 5th drops off).
        The block's first value (laps driven on the track, a statistic) is not touched."""
        if len(laps) > 10:
            raise ValueError("At most 10 laps")
        for stat, cs in laps:
            if not 1 <= cs <= 65534:
                raise ValueError("Lap times must be between 0:00.01 and 10:55.34")
            if not 0 <= stat <= 0xFFFFFFFF:
                raise ValueError("Speed pads must be 0 or more")
        if laps:
            if not ship:
                raise ValueError("Pick a ship")
            if len(ship.encode("latin1")) > 31 or len(pilot.encode("latin1")) > 7:
                raise ValueError("Ship name: max 31 characters, pilot name: max 7")
        if not laps:
            for p in range(pos, RECORD_POSITIONS - 1):
                self._copy_entry(b, cls, p + 1, p)
            self._clear_entry(b, cls, RECORD_POSITIONS - 1)
            return
        if push:
            for p in range(RECORD_POSITIONS - 1, pos, -1):
                self._copy_entry(b, cls, p - 1, p)
        base = self.entry_off(b, cls, pos)
        for i in range(10):
            o = base + 8 * i
            stat, cs = laps[i] if i < len(laps) else (0, 0xFFFF)
            self.set(o, "u32", stat)
            self.set(o + 4, "u16", cs)
            self.set(o + 6, "u16", 0)
        self.set_str(base + 0x50, 0x20, ship)
        self.set_str(base + 0x70, 8, pilot)

    def default_pilot(self):
        for b in self.track_blocks():
            for c in range(len(SPEED_CLASSES)):
                r = self.track_record(b, c, 0)
                if r["valid"] and r["pilot"]:
                    return r["pilot"]
        return "Player"

    # -- HD / Fury campaign -------------------------------------------------------------------
    def cells(self, campaign=None):
        res = []
        for b in self.blocks:
            info = CELLS.get(b.name)
            if info and b.size == 0x48 and (campaign is None or info[0] == campaign):
                res.append((b, info))
        return res

    def cell_state(self, b):
        return {"best": self.get(b.data), "best2": self.get(b.data + 4),
                "diff": self.get(b.data + 8, "u8"), "medal": self.get(b.data + 9, "u8")}

    def set_cell(self, b, medal, diff=2):
        """Set a medal (0 gold .. 2 bronze, 0xFF none) and fill a matching best result."""
        info = CELLS[b.name]
        _, _, mode, laps, lower, target = info
        if medal == 0xFF:
            self.set(b.data + 8, "u8", 0xFF)
            self.set(b.data + 9, "u8", 0xFF)
            return
        self.set(b.data + 8, "u8", diff)
        self.set(b.data + 9, "u8", medal)
        if mode in TIMED_MODES:
            # 0:00.01 per lap: Time Trial keeps the race time and the best lap, Speed Lap the lap.
            total = max(1, laps) if mode == "Time Trial" else 1
            self.set(b.data, "u32", total)
            self.set(b.data + 4, "u32", 1)
        elif lower:                          # Race, Tournament, Elimination, NitroBattle: position
            self.set(b.data, "u32", 1 + medal)
        else:                                # Zone, Detonator: score, higher is better
            self.set(b.data, "u32", target)

    def campaign_summary(self, campaign):
        cells = self.cells(campaign)
        golds = sum(1 for b, _ in cells if self.get(b.data + 9, "u8") == 0)
        elite = sum(1 for b, _ in cells if self.get(b.data + 9, "u8") == 0 and self.get(b.data + 8, "u8") == 2)
        points = sum(3 - m for b, _ in cells for m in [self.get(b.data + 9, "u8")] if m < 3)
        return {"cells": len(cells), "gold": golds, "elite_gold": elite, "points": points}

    # -- WipEout 2048 campaign (records in the Mjolnir block) ------------------------------------
    def events_2048(self):
        """[(instance id, season, name, data offset)] for every 2048 event record in the file.

        Mjolnir holds SP.xml instances as: u32 instance id, u32 type, then fields
        (u32 tag, u32 length, data). Event records start with tag 0x3C0 (4 bytes)."""
        b = self.by_name.get("Mjolnir")
        if not b:
            return []
        M = bytes(self.buf[b.data:b.data + b.size])
        out = []
        sig = struct.pack("<II", EV_STATE, 4)
        for iid, (season, name) in EVENTS_2048.items():
            key = struct.pack("<I", iid)
            p = M.find(key)
            while p >= 0:
                if M[p + 8:p + 16] == sig:
                    out.append((iid, season, name, b.data + p))
                    break
                p = M.find(key, p + 1)
        order = {k: i for i, k in enumerate(EVENTS_2048)}
        out.sort(key=lambda r: order[r[0]])
        return out

    def _ev_field(self, rec_off, tag):
        """File offset of a field's data in the event record at rec_off, or None."""
        p = rec_off + 8
        for _ in range(16):
            t, ln = struct.unpack_from("<II", self.buf, p)
            if t == tag:
                return p + 8
            if t < 0x40 or ln > 0x400:
                return None
            p += 8 + ln
        return None

    def event_state(self, rec_off):
        st, done, res = (self._ev_field(rec_off, t) for t in (EV_STATE, EV_DONE, EV_RESULT))
        return {"state": self.get(st) if st else None,
                "done": self.get(done, "u8") if done else None,
                "result": self.get(res) if res else None}

    def set_event(self, rec_off, unlock=None, complete=None, result=None, best=None):
        """best: (iid, ship, livery) to also write a 0:00.01-per-lap best result."""
        st, done, res = (self._ev_field(rec_off, t) for t in (EV_STATE, EV_DONE, EV_RESULT))
        if unlock is not None and st:
            if unlock:
                if self.get(st) < 2:
                    self.set(st, "u32", 2)
            else:
                self.set(st, "u32", 0)
        if complete is not None and done:
            self.set(done, "u8", 1 if complete else 0)
            if st:
                if complete:
                    self.set(st, "u32", EV_PLAYED)
                elif self.get(st) == EV_PLAYED:
                    self.set(st, "u32", 2)
            if res is not None and not complete:
                self.set(res, "u32", 0)
        if result is not None and res:
            self.set(res, "u32", result)
        if best and complete:
            self.write_event_best(rec_off, *best)

    def write_event_best(self, rec_off, iid, ship, livery):
        laps = max(1, min(10, EVENT_LAPS.get(iid, 3)))
        name = EVENTS_2048.get(iid, ("", ""))[1]
        race = self._ev_field(rec_off, EV_BEST_RACE)
        if race:
            for i in range(10):
                o = race + 8 * i
                self.set(o, "u32", 1 if i < laps else 0)
                self.set(o + 4, "u16", 1 if i < laps else 0xFFFF)
                self.set(o + 6, "u16", 0)
            self.set_str(race + 0x9C, 0x20, livery)
            self.set_str(race + 0xBC, 0x20, ship)
            return
        tbest = self._ev_field(rec_off, EV_BEST_TIME)
        if tbest:
            self.set_str(tbest + 4, 0x20, livery)
            self.set_str(tbest + 0x24, 0x20, ship)
            if self.get(tbest + 0x44) == 0:
                self.set(tbest + 0x44, "u32", 1)
            self.set(tbest + 0x48, "u32", 1 if "Speed Lap" in name else laps)

    def campaign_2048_summary(self):
        ev = [e for e in self.events_2048() if e[1] in ("2048", "2049", "2050")]
        states = [self.event_state(e[3]) for e in ev]
        return {"events": len(ev), "unlocked": sum(1 for s in states if (s["state"] or 0) >= 2),
                "done": sum(1 for s in states if s["done"]),
                "seasons": {y: any((self.event_state(e[3])["state"] or 0) >= 2
                                   for e in ev if e[1] == y) for y in ("2048", "2049", "2050")}}

    # -- settings ----------------------------------------------------------------------------
    def settings(self):
        res = []
        for b in self.blocks:
            if b.size == 0x40 and not b.name.startswith("~"):
                raw = bytes(self.buf[b.data:b.data + 0x40]).split(b"\0")[0]
                if all(32 <= c < 127 for c in raw):
                    res.append((b, raw.decode("latin1")))
        return res

    # -- writing -----------------------------------------------------------------------------
    def save(self, backup_root=None):
        """Back up the files on disk, then write. Returns the backup folder."""
        backup = None
        if backup_root:
            backup = backup_profile(self.path, backup_root, "editor")
        with open(self.path, "r+b") as f:
            f.write(self.buf)
        self.original = bytes(self.buf)
        return backup


# ----------------------------------------------------------------------------------------------
# Backups / snapshots
# ----------------------------------------------------------------------------------------------
def profile_dirs(data_path):
    """DATA.BIN -> (profile dir, matching .sce_sys dir or None, path relative to savedata)."""
    prof = os.path.dirname(os.path.abspath(data_path))
    sce = prof + ".sce_sys"
    if not os.path.isdir(sce):
        sce = None
    parts = prof.replace("\\", "/").split("/")
    try:
        i = len(parts) - 1 - parts[::-1].index("savedata")
        rel = os.path.join(*parts[i + 1:])
    except ValueError:
        rel = os.path.basename(prof)
    return prof, sce, rel


def backup_profile(data_path, root, tag):
    prof, sce, rel = profile_dirs(data_path)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root, "%s-%s" % (stamp, tag))
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(root, "%s-%s-%d" % (stamp, tag, n))
    shutil.copytree(prof, os.path.join(dest, rel))
    if sce:
        shutil.copytree(sce, os.path.join(dest, rel + ".sce_sys"))
    return dest


def user_dir_for(data_path):
    p = os.path.abspath(data_path).replace("\\", "/")
    i = p.rfind("/savedata/")
    return p[:i] if i >= 0 else os.path.dirname(os.path.dirname(os.path.abspath(data_path)))


PREFS_PATH = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "wipeout-save-editor",
                          "editor.json")


def load_prefs():
    try:
        import json
        with open(PREFS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_prefs(**kw):
    try:
        import json
        prefs = load_prefs()
        prefs.update(kw)
        os.makedirs(os.path.dirname(PREFS_PATH), exist_ok=True)
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=1)
    except Exception:
        pass


def savedata_root_of(data_path):
    """<savedata> folder of .../savedata/<user>/CUSA05670/PROFILExx/DATA.BIN."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(data_path)))))


def candidate_roots():
    """Places to look for saves, without any machine-specific path: the last save used, then folders
    relative to the editor (when it sits inside or next to the game folder) and to the current folder."""
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    roots = []
    last = load_prefs().get("last_save")
    if last and os.path.isfile(last):
        roots.append(savedata_root_of(last))
    for base in (os.path.join(here, ".."), here, os.getcwd()):
        roots.append(os.path.join(base, "user", "savedata"))
    out = []
    for r in roots:
        r = os.path.normpath(r)
        if r not in out:
            out.append(r)
    return out


def find_saves():
    last = load_prefs().get("last_save")
    if last and os.path.isfile(last):
        return [last]
    found = []
    for r in candidate_roots():
        r = os.path.normpath(r)
        if not os.path.isdir(r):
            continue
        for dp, dn, fn in os.walk(r):
            if "DATA.BIN" in fn and "CUSA" in dp.upper():
                found.append(os.path.join(dp, "DATA.BIN"))
        if found:
            break
    return sorted(set(found))


TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")
TITLE_ID = "CUSA05670"


def template_profile():
    """(profile folder, its .sce_sys folder) of the clean save captured from the game, or None."""
    base = os.path.join(TEMPLATE_DIR, TITLE_ID)
    if not os.path.isdir(base):
        return None
    for name in sorted(os.listdir(base)):
        prof = os.path.join(base, name)
        if os.path.isfile(os.path.join(prof, "DATA.BIN")) and os.path.isdir(prof + ".sce_sys"):
            return prof, prof + ".sce_sys"
    return None


def default_savedata_root():
    """A sensible folder to start the folder picker in (may not exist yet), or None."""
    for r in candidate_roots():
        if os.path.isdir(os.path.dirname(r)):
            return r
    return None


def resolve_savedata_root(chosen):
    """Turn the folder the user picked into the 'savedata' folder the save belongs in:
    the game folder (contains user\\savedata), its user folder, a savedata folder, or any folder."""
    chosen = os.path.normpath(chosen)
    if os.path.basename(chosen).lower() == "savedata":
        return chosen
    if os.path.isdir(os.path.join(chosen, "user")):
        return os.path.join(chosen, "user", "savedata")
    if os.path.isdir(os.path.join(chosen, "savedata")) or os.path.basename(chosen).lower() == "user":
        return os.path.join(chosen, "savedata")
    return chosen


def pick_user_id(savedata_root):
    """Existing numeric user folder in savedata (the game's user id), else '1'."""
    try:
        ids = sorted(d for d in os.listdir(savedata_root)
                     if d.isdigit() and os.path.isdir(os.path.join(savedata_root, d)))
        if ids:
            return ids[0]
    except OSError:
        pass
    return "1"


def create_new_save(savedata_root, user_id="1", backup_root=None):
    """Copy the clean template profile into <savedata>/<user>/CUSA05670/. An existing profile with the
    same name is backed up first (to backup_root) and replaced. Returns the new DATA.BIN path."""
    tpl = template_profile()
    if not tpl:
        raise ValueError("No clean template save found in %s." % TEMPLATE_DIR)
    prof, sce = tpl
    dest_dir = os.path.join(savedata_root, user_id, TITLE_ID)
    dest = os.path.join(dest_dir, os.path.basename(prof))
    if os.path.exists(os.path.join(dest, "DATA.BIN")):
        if backup_root:
            backup_profile(os.path.join(dest, "DATA.BIN"), backup_root, "before-new-save")
        shutil.rmtree(dest)
        if os.path.isdir(dest + ".sce_sys"):
            shutil.rmtree(dest + ".sce_sys")
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copytree(prof, dest)
    shutil.copytree(sce, dest + ".sce_sys")
    return os.path.join(dest, "DATA.BIN")


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
    except Exception:
        return False
    return any(n in out for n in ("gow3-probe.exe", "shadps4.exe"))


# ----------------------------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------------------------
SIDEBAR_BG = "#1d2330"
SIDEBAR_FG = "#c9d1e0"
SIDEBAR_HOVER = "#2b3446"
ACCENT = "#2f7cf6"
TOPBAR_BG = "#eef1f6"


class EditDialog(simpledialog.Dialog):
    def __init__(self, parent, title, prompt, initial, choices=None):
        self.prompt, self.initial, self.choices, self.result = prompt, initial, choices, None
        super().__init__(parent, title)

    def body(self, master):
        ttk.Label(master, text=self.prompt, justify="left").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.var = tk.StringVar(value=self.initial)
        if self.choices:
            self.entry = ttk.Combobox(master, textvariable=self.var, values=self.choices, width=36)
        else:
            self.entry = ttk.Entry(master, textvariable=self.var, width=38)
        self.entry.grid(row=1, column=0, sticky="we")
        self.entry.select_range(0, "end")
        return self.entry

    def apply(self):
        self.result = self.var.get()


class App(tk.Tk):
    PAGES = [("quick", "Quick actions"), ("campaign", "HD / Fury medals"), ("c2048", "2048 campaign"),
             ("records", "Track records"),
             ("ships", "Ships"), ("stats", "Profile stats"), ("settings", "Settings"),
             ("raw", "Advanced")]

    def __init__(self, path=None):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1220x780")
        self.minsize(1000, 620)
        try:
            ttk.Style(self).theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
        st = ttk.Style(self)
        st.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        st.configure("Sub.TLabel", foreground="#5a6475")
        st.configure("Game.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        st.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=(10, 6))
        # rows tall enough for the font at any display scaling
        import tkinter.font as tkfont
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        st.configure("Treeview", rowheight=int(line * 1.45))
        # window size in proportion to the display scaling, within the screen
        f = max(1.0, float(self.tk.call("tk", "scaling")) / 1.3333)
        w = min(int(1250 * f), self.winfo_screenwidth() - 40)
        h = min(int(820 * f), self.winfo_screenheight() - 80)
        self.geometry("%dx%d" % (w, h))
        self.save = None
        self.page = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        saves = find_saves()
        path = path or (saves[0] if saves else None)
        if path:
            self.load(path)
        else:
            self.status("No save found. Use 'New save...' to create a clean one, or 'Open save...'.")
            if template_profile():
                self.after(300, self.offer_new_save)
        self.show_page("quick")

    def offer_new_save(self):
        if messagebox.askyesno(APP_TITLE, "No WipEout Omega save was found.\n\nCreate a new, clean save now?\n"
                               "(It is the same file the game makes on a first start. You choose where.)"):
            self.on_new_save()

    # -- layout --------------------------------------------------------------------------------
    def _build(self):
        # top bar: file actions only
        top = tk.Frame(self, bg=TOPBAR_BG, bd=0, highlightthickness=0)
        top.pack(fill="x")
        inner = ttk.Frame(top, padding=(10, 8), style="Top.TFrame")
        ttk.Style(self).configure("Top.TFrame", background=TOPBAR_BG)
        ttk.Style(self).configure("Top.TLabel", background=TOPBAR_BG, foreground="#5a6475")
        inner.pack(fill="x")
        ttk.Label(inner, text="FILE", style="Top.TLabel", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 8))
        ttk.Button(inner, text="Open save...", command=self.on_open).pack(side="left")
        ttk.Button(inner, text="New save...", command=self.on_new_save).pack(side="left", padx=(4, 0))
        ttk.Button(inner, text="Reload", command=self.on_reload).pack(side="left", padx=4)
        ttk.Button(inner, text="Save changes", style="Big.TButton", command=self.on_save).pack(side="left", padx=(4, 0))
        ttk.Separator(inner, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(inner, text="Save snapshot...", command=self.on_snapshot).pack(side="left")
        ttk.Button(inner, text="Restore snapshot / backup...", command=self.on_restore).pack(side="left", padx=4)
        self.path_lbl = ttk.Label(inner, text="", style="Top.TLabel")
        self.path_lbl.pack(side="left", padx=10)
        tk.Frame(self, height=1, bg="#c8ced8").pack(fill="x")

        body = tk.Frame(self)
        body.pack(fill="both", expand=True)

        # left sidebar: the page menu
        side = tk.Frame(body, bg=SIDEBAR_BG, width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="WipEout\nSave Editor", bg=SIDEBAR_BG, fg="white", justify="left",
                 font=("Segoe UI", 14, "bold"), anchor="w", padx=18, pady=16).pack(fill="x")
        tk.Label(side, text="MENU", bg=SIDEBAR_BG, fg="#7d879a", font=("Segoe UI", 8, "bold"),
                 anchor="w", padx=18).pack(fill="x", pady=(4, 4))
        self.nav = {}
        for key, label in self.PAGES:
            w = tk.Label(side, text=label, bg=SIDEBAR_BG, fg=SIDEBAR_FG, font=("Segoe UI", 11),
                         anchor="w", padx=18, pady=9, cursor="hand2")
            w.pack(fill="x")
            w.bind("<Button-1>", lambda e, k=key: self.show_page(k))
            w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
            w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self.nav[key] = w

        # content
        self.content = ttk.Frame(body, padding=(18, 12, 18, 6))
        self.content.pack(side="left", fill="both", expand=True)
        self.page_title = ttk.Label(self.content, text="", style="Title.TLabel")
        self.page_title.pack(anchor="w")
        self.page_sub = ttk.Label(self.content, text="", style="Sub.TLabel", wraplength=900, justify="left")
        self.page_sub.pack(anchor="w", pady=(0, 8))
        self.stack = ttk.Frame(self.content)
        self.stack.pack(fill="both", expand=True)
        self.frames = {k: ttk.Frame(self.stack) for k, _ in self.PAGES}
        self._build_quick(self.frames["quick"])
        self._build_campaign(self.frames["campaign"])
        self._build_c2048(self.frames["c2048"])
        self._build_records(self.frames["records"])
        self._build_ships(self.frames["ships"])
        self._build_stats(self.frames["stats"])
        self._build_settings(self.frames["settings"])
        self._build_raw(self.frames["raw"])

        self.status_var = tk.StringVar()
        tk.Frame(self, height=1, bg="#c8ced8").pack(fill="x")
        ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(10, 4)).pack(fill="x")

    SUBTITLES = {
        "quick": "One-click changes for each game. Nothing is written until you press Save changes.",
        "campaign": "WipEout HD and HD Fury campaign events (167). Select one or more rows, then set a medal.",
        "c2048": "WipEout 2048 campaign: seasons 2048, 2049 and 2050, ship challenges and speed laps. 2049 opens "
                 "when 2048 - Event 7 is completed, 2050 when 2049 - Event 8 is.",
        "records": "Best race on each track: lap times, ship and pilot. Create records on any track, "
                   "or apply one ship and set of lap times to many tracks at once.",
        "ships": "Per-ship counters. Double-click a value to change it.",
        "stats": "Profile header counters. Double-click to edit.",
        "settings": "Game options as the game stores them (text). Double-click to edit.",
        "raw": "Every block in the file with a hex view; read or write a single value.",
    }

    def show_page(self, key):
        for k, f in self.frames.items():
            f.pack_forget()
        self.frames[key].pack(fill="both", expand=True)
        self.page = key
        self.page_title.config(text=dict(self.PAGES)[key])
        self.page_sub.config(text=self.SUBTITLES[key])
        for k, w in self.nav.items():
            sel = k == key
            w.config(bg=ACCENT if sel else SIDEBAR_BG, fg="white" if sel else SIDEBAR_FG,
                     font=("Segoe UI", 11, "bold" if sel else "normal"))

    def _nav_hover(self, key, on):
        if key != self.page:
            self.nav[key].config(bg=SIDEBAR_HOVER if on else SIDEBAR_BG)

    def _tree(self, parent, cols, widths, height=18, select="browse"):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=height, selectmode=select)
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w", stretch=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return frame, tree

    # -- quick actions ---------------------------------------------------------------------------
    def _build_quick(self, f):
        who = ttk.Frame(f)
        who.pack(fill="x", pady=(0, 8))
        ttk.Label(who, text="Player name (max 7 characters, used on records):").pack(side="left")
        self.q_pilot = tk.StringVar(value="Player")
        ttk.Entry(who, textvariable=self.q_pilot, width=12).pack(side="left", padx=6)
        ttk.Label(who, text="Ship for records:").pack(side="left", padx=(16, 0))
        self.q_ship = tk.StringVar(value="Feisar")
        ttk.Combobox(who, textvariable=self.q_ship, values=SHIPS, width=16, state="readonly").pack(side="left", padx=6)
        ttk.Label(who, text="2048 ship:").pack(side="left", padx=(16, 0))
        self.q48_ship = tk.StringVar(value="Feisar2048")
        ttk.Combobox(who, textvariable=self.q48_ship, values=SHIPS_2048, width=15, state="readonly").pack(
            side="left", padx=6)
        self.q48_livery = tk.StringVar(value="speed")
        ttk.Combobox(who, textvariable=self.q48_livery, values=LIVERIES_2048, width=10, state="readonly").pack(
            side="left")

        self.q_status = {}

        def game_box(title, rows, key):
            box = ttk.LabelFrame(f, text=title, style="Game.TLabelframe", padding=10)
            box.pack(fill="x", pady=5)
            for text, cmd in rows:
                ttk.Button(box, text=text, command=cmd).pack(side="left", padx=(0, 8))
            lab = ttk.Label(box, text="", style="Sub.TLabel")
            lab.pack(side="left", padx=10)
            self.q_status[key] = lab
            return box

        game_box("WipEout HD", [("Set max rank: gold on all HD events (Elite)", lambda: self.q_gold("HD"))], "HD")
        game_box("WipEout HD Fury", [("Set max rank: gold on all Fury events (Elite)", lambda: self.q_gold("Fury"))],
                 "Fury")
        game_box("WipEout 2048", [("Set max rank (50)", self.q_max_2048),
                                  ("Elite (gold) on all events, 0:00.01 laps", self.q_2048_all)], "2048")
        rb = game_box("Track records", [("Records on every track: 0:00.01 per lap", self.q_records)], "records")
        ttk.Label(rb, text="Speed class:").pack(side="left", padx=(10, 4))
        self.q_class = tk.StringVar(value=ALL_CLASSES)
        ttk.Combobox(rb, textvariable=self.q_class, values=[ALL_CLASSES] + SPEED_CLASSES, width=32,
                     state="readonly").pack(side="left")

        allbox = ttk.Frame(f, padding=(0, 10))
        allbox.pack(fill="x")
        ttk.Button(allbox, text="Do all of the above", style="Big.TButton", command=self.q_all).pack(side="left")
        ttk.Label(allbox, text="  then press Save changes at the top.", style="Sub.TLabel").pack(side="left")

        ttk.Label(f, style="Sub.TLabel", wraplength=900, justify="left", text=(
            "Notes\n"
            "- WipEout HD and HD Fury have no XP rank: progress is the medal and points grid. 'Max rank' sets a "
            "gold medal at Elite difficulty on every campaign event, which gives every campaign point (3 per gold) "
            "and opens every grid. Timed events get 0:00.01 per lap, races 1st place, Zone and Detonator the Elite "
            "gold score.\n"
            "- WipEout 2048 rank comes from XP (stat 199; rank 50 = 1,790,000 XP). 'Unlock + complete all events' "
            "opens every event of 2048, 2049 and 2050 (plus the speed laps and ship challenges) and marks them "
            "completed the way the game does for a passed event.\n"
            "- Records use 0:00.01 per lap (3 laps) on every track including Zone 1-4, with the ship and name "
            "above.\n"
            "- Close the game before saving; the game rewrites the file when it saves.")).pack(fill="x", pady=8)

    def q_gold(self, campaign, quiet=False):
        if not self.require():
            return
        n = 0
        for b, info in self.save.cells(campaign):
            self.save.set_cell(b, 0, 2)
            n += 1
        self.after_change("Gold (Elite) set on %d %s events." % (n, campaign))

    def q_max_2048(self):
        if not self.require():
            return
        self.save.set(XP_OFFSET, "u32", MAX_XP)
        self.after_change("2048 XP set to %d (rank 50)." % MAX_XP)

    def q_2048_all(self):
        if not self.require():
            return
        ev = self.save.events_2048()
        if not ev:
            messagebox.showerror(APP_TITLE, "No 2048 campaign records found in this save.")
            return
        best = (self.q48_ship.get(), self.q48_livery.get())
        for iid, season, name, off in ev:
            self.save.set_event(off, unlock=True, complete=True, result=2, best=(iid,) + best)
        self.after_change("%d WipEout 2048 events played with Elite (gold), 0:00.01 per lap, %s %s." % (
            len(ev), best[0], best[1]))

    def q_records(self):
        if not self.require():
            return
        try:
            n = self.apply_records_to(self.target_tracks("all"), None,
                                      self.q_ship.get(), self.q_pilot.get().strip() or "Player",
                                      classes=self.class_indexes(self.q_class.get()), auto=(1, [1]))
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.after_change("Records set on %d tracks, %s (0:00.01 per lap)." % (n, self.q_class.get()))

    def q_all(self):
        if not self.require():
            return
        try:
            pilot = self.q_pilot.get().strip() or "Player"
            if len(pilot.encode("latin1")) > 7:
                raise ValueError("Player name: max 7 characters")
            for c in ("HD", "Fury"):
                for b, info in self.save.cells(c):
                    self.save.set_cell(b, 0, 2)
            self.save.set(XP_OFFSET, "u32", MAX_XP)
            for iid, season, name, off in self.save.events_2048():
                self.save.set_event(off, unlock=True, complete=True, result=2,
                                    best=(iid, self.q48_ship.get(), self.q48_livery.get()))
            n = self.apply_records_to(self.target_tracks("all"), None, self.q_ship.get(), pilot,
                                      classes=self.class_indexes(self.q_class.get()), auto=(1, [1]))
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.after_change("HD + Fury gold on everything, 2048 rank 50 with all events done, records on %d tracks. "
                          "Press Save changes." % n)

    def refresh_quick(self):
        s = self.save
        if not s:
            return
        for c in ("HD", "Fury"):
            m = s.campaign_summary(c)
            self.q_status[c].config(text="%d/%d gold (%d at Elite), %d campaign points" % (
                m["gold"], m["cells"], m["elite_gold"], m["points"]))
        xp = s.get(XP_OFFSET)
        c = s.campaign_2048_summary()
        open_s = "/".join(y for y, v in c["seasons"].items() if v) or "none"
        self.q_status["2048"].config(text="XP %d, rank %d. Seasons open: %s; %d/%d events completed" % (
            xp, rank_for_xp(xp), open_s, c["done"], c["events"]))
        recs = [b for b in s.track_blocks() if s.any_record(b)]
        self.q_status["records"].config(text="%d of %d tracks have a record" % (len(recs), len(s.track_blocks())))

    def after_change(self, msg):
        self.refresh_all()
        self.status(msg + " (not saved yet)")

    # -- campaign --------------------------------------------------------------------------------
    def _build_campaign(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Show:").pack(side="left")
        self.camp_filter = tk.StringVar(value="All")
        cb = ttk.Combobox(top, textvariable=self.camp_filter, values=["All", "HD", "Fury"], width=6, state="readonly")
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_campaign())
        self.camp_sum = ttk.Label(top, text="", style="Sub.TLabel")
        self.camp_sum.pack(side="left", padx=12)
        fr, self.camp_tree = self._tree(f, ("Game", "Event", "Track", "Mode", "Medal", "Difficulty", "Best"),
                                        (60, 90, 220, 110, 70, 80, 120), select="extended")
        fr.pack(fill="both", expand=True, pady=6)
        bot = ttk.Frame(f)
        bot.pack(fill="x")
        ttk.Label(bot, text="Selected events:").pack(side="left")
        self.camp_medal = tk.StringVar(value="Gold")
        ttk.Combobox(bot, textvariable=self.camp_medal, values=list(MEDAL_IDS), width=8, state="readonly").pack(
            side="left", padx=4)
        ttk.Label(bot, text="on").pack(side="left")
        self.camp_diff = tk.StringVar(value="Elite")
        ttk.Combobox(bot, textvariable=self.camp_diff, values=list(DIFF_IDS), width=8, state="readonly").pack(
            side="left", padx=4)
        ttk.Button(bot, text="Apply to selected", command=self.camp_apply).pack(side="left", padx=6)
        ttk.Button(bot, text="Select all shown", command=lambda: self.camp_tree.selection_set(
            self.camp_tree.get_children())).pack(side="left")

    def refresh_campaign(self):
        tree = self.camp_tree
        tree.delete(*tree.get_children())
        if not self.save:
            return
        flt = self.camp_filter.get()
        for b, info in self.save.cells(None if flt == "All" else flt):
            camp, track, mode, laps, lower, target = info
            st = self.save.cell_state(b)
            best = st["best"]
            if best in (0xFFFFFFFF,) or (best == 0 and lower):
                bt = "--"
            elif mode in TIMED_MODES:
                bt = fmt_time(best)
            elif lower:
                bt = ordinal(best)
            else:
                bt = str(best)
            tree.insert("", "end", iid=b.name, values=(
                camp, b.name, TRACKS.get(track, track) if track and track != "None" else "(several tracks)", mode, MEDALS.get(st["medal"], "?%d" % st["medal"]),
                DIFFS.get(st["diff"], str(st["diff"])), bt))
        parts = []
        for c in ("HD", "Fury"):
            m = self.save.campaign_summary(c)
            parts.append("%s: %d/%d gold, %d points" % (c, m["gold"], m["cells"], m["points"]))
        self.camp_sum.config(text="   ".join(parts))

    def camp_apply(self):
        sel = self.camp_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "Select one or more events first (Ctrl/Shift-click).")
            return
        medal = MEDAL_IDS[self.camp_medal.get()]
        diff = DIFF_IDS[self.camp_diff.get()]
        for name in sel:
            self.save.set_cell(self.save.by_name[name], medal, diff)
        self.after_change("%s set on %d events." % (self.camp_medal.get(), len(sel)))
        for name in sel:
            if self.camp_tree.exists(name):
                self.camp_tree.selection_add(name)

    # -- 2048 campaign ---------------------------------------------------------------------------------
    def _build_c2048(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Show:").pack(side="left")
        self.c48_filter = tk.StringVar(value="All")
        cb = ttk.Combobox(top, textvariable=self.c48_filter, width=12, state="readonly",
                          values=["All", "2048", "2049", "2050", "Challenge", "Speed Lap"])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_c2048())
        self.c48_sum = ttk.Label(top, text="", style="Sub.TLabel")
        self.c48_sum.pack(side="left", padx=12)
        fr, self.c48_tree = self._tree(f, ("Season", "Event", "State", "Completed", "Medal", "Best", "Ship"),
                                       (80, 260, 90, 85, 90, 120, 160), select="extended")
        fr.pack(fill="both", expand=True, pady=6)
        opt = ttk.Frame(f)
        opt.pack(fill="x")
        ttk.Label(opt, text="Ship for best results:").pack(side="left")
        ttk.Combobox(opt, textvariable=self.q48_ship, values=SHIPS_2048, width=15, state="readonly").pack(
            side="left", padx=4)
        ttk.Label(opt, text="Livery:").pack(side="left", padx=(10, 0))
        ttk.Combobox(opt, textvariable=self.q48_livery, values=LIVERIES_2048, width=10, state="readonly").pack(
            side="left", padx=4)
        bot = ttk.Frame(f)
        bot.pack(fill="x", pady=(6, 0))
        ttk.Label(bot, text="Selected:").pack(side="left")
        for text, kw in (("Unlock", dict(unlock=True)), ("Lock", dict(unlock=False, complete=False)),
                         ("Elite (gold), 0:00.01 laps", dict(unlock=True, complete=True, result=2, best=True)),
                         ("Pass, 0:00.01 laps", dict(unlock=True, complete=True, result=1, best=True)),
                         ("Reset to unplayed", dict(complete=False))):
            ttk.Button(bot, text=text, command=lambda kw=kw, t=text: self.c48_apply(kw, t)).pack(side="left", padx=3)
        ttk.Button(bot, text="Select all shown", command=lambda: self.c48_tree.selection_set(
            self.c48_tree.get_children())).pack(side="left", padx=(12, 0))
        ttk.Label(f, style="Sub.TLabel", wraplength=1000, justify="left", text=(
            "Each event's record: state (0 locked, 2 unlocked, 4 played), completed flag, medal (1 Pass, 2 Elite, "
            "shown in game as ELITE PASS) and the best result (laps or time, ship and livery). Races, time trials "
            "and speed laps get a best of 0:00.01 per lap; zone and combat events only get the medal (their result "
            "layout is not known yet). 'Lock' on 2048 - Event 1 would leave nothing to play: avoid it.")).pack(
            anchor="w", pady=(6, 0))

    RESULT_NAMES = {0: "-", 1: "Pass", 2: "Elite"}

    def event_best_text(self, off):
        s = self.save
        race = s._ev_field(off, EV_BEST_RACE)
        if race:
            laps = [s.get(race + 8 * i + 4, "u16") for i in range(10)]
            laps = [t for t in laps if t != 0xFFFF]
            ship = s.get_str(race + 0xBC, 0x20)
            return (fmt_time(sum(laps)) + " (%d laps)" % len(laps) if laps else "--"), ship
        t = s._ev_field(off, EV_BEST_TIME)
        if t:
            v = s.get(t + 0x48)
            return (fmt_time(v) if v else "--"), s.get_str(t + 0x24, 0x20)
        return "(not decoded)", ""

    def refresh_c2048(self):
        tree = self.c48_tree
        sel = tree.selection()
        tree.delete(*tree.get_children())
        if not self.save:
            return
        flt = self.c48_filter.get()
        for iid, season, name, off in self.save.events_2048():
            if flt != "All" and season != flt:
                continue
            st = self.save.event_state(off)
            state = {0: "Locked", 2: "Unlocked", 4: "Played"}.get(st["state"], str(st["state"]))
            best, ship = self.event_best_text(off)
            tree.insert("", "end", iid=str(off), values=(
                season, name, state, "yes" if st["done"] else "-",
                self.RESULT_NAMES.get(st["result"], str(st["result"])), best, ship))
        for s in sel:
            if tree.exists(s):
                tree.selection_add(s)
        c = self.save.campaign_2048_summary()
        self.c48_sum.config(text="Seasons: %s.  %d of %d season events unlocked, %d completed." % (
            ", ".join("%s %s" % (y, "open" if v else "locked") for y, v in c["seasons"].items()),
            c["unlocked"], c["events"], c["done"]))

    def c48_apply(self, kw, label):
        sel = self.c48_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "Select one or more events first (Ctrl/Shift-click, or Select all shown).")
            return
        ids = {e[3]: e[0] for e in self.save.events_2048()}
        for off in sel:
            k = dict(kw)
            if k.get("best"):
                k["best"] = (ids[int(off)], self.q48_ship.get(), self.q48_livery.get())
            self.save.set_event(int(off), **k)
        self.after_change("%s: %d events." % (label, len(sel)))

    # -- records -----------------------------------------------------------------------------------
    def _build_records(self, f):
        # bottom strip: one ship + lap times applied to many tracks
        bulk = ttk.LabelFrame(f, text="Apply one ship and set of lap times to many tracks",
                              style="Game.TLabelframe", padding=10)
        bulk.pack(side="bottom", fill="x", pady=(8, 0))
        row1 = ttk.Frame(bulk)
        row1.pack(fill="x")
        ttk.Label(row1, text="Ship").pack(side="left")
        self.b_ship = tk.StringVar(value="Feisar")
        ttk.Combobox(row1, textvariable=self.b_ship, values=SHIPS, width=15, state="readonly").pack(
            side="left", padx=(4, 14))
        ttk.Label(row1, text="Pilot").pack(side="left")
        self.b_pilot = tk.StringVar(value="Player")
        ttk.Entry(row1, textvariable=self.b_pilot, width=10).pack(side="left", padx=(4, 14))
        ttk.Label(row1, text="Laps").pack(side="left")
        self.b_laps = tk.IntVar(value=3)
        sp = ttk.Spinbox(row1, from_=1, to=10, textvariable=self.b_laps, width=4, command=self._bulk_laps)
        sp.pack(side="left", padx=(4, 4))
        sp.bind("<KeyRelease>", lambda e: self._bulk_laps())
        self.b_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="Auto (class lap count)", variable=self.b_auto, command=self._bulk_laps).pack(
            side="left", padx=(0, 14))
        ttk.Label(row1, text="Apply to").pack(side="left")
        self.b_scope = tk.StringVar(value="All tracks (incl. Zone 1-4)")
        ttk.Combobox(row1, textvariable=self.b_scope, width=26, state="readonly", values=[
            "All tracks (incl. Zone 1-4)", "Race tracks (no Zone 1-4)", "HD / Fury tracks", "2048 tracks",
            "Zone 1-4", "Selected tracks", "Tracks with a record"]).pack(
            side="left", padx=(4, 14))
        ttk.Button(row1, text="Apply to tracks", style="Big.TButton", command=self.bulk_apply).pack(side="left")
        row1b = ttk.Frame(bulk)
        row1b.pack(fill="x", pady=(8, 0))
        ttk.Label(row1b, text="Speed class").pack(side="left")
        self.b_class = tk.StringVar(value=ALL_CLASSES)
        ttk.Combobox(row1b, textvariable=self.b_class, values=[ALL_CLASSES] + SPEED_CLASSES, width=32,
                     state="readonly").pack(side="left", padx=(4, 14))
        self.b_push = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1b, text="Keep existing times (move them down the top 5)", variable=self.b_push).pack(
            side="left")
        ttk.Label(row1b, text="   Speed pads per lap").pack(side="left")
        self.b_pads = tk.StringVar(value="1")
        ttk.Entry(row1b, textvariable=self.b_pads, width=5).pack(side="left", padx=4)
        row2 = ttk.Frame(bulk)
        row2.pack(fill="x", pady=(8, 0))
        self.b_times = []
        self.b_time_widgets = []
        for i in range(10):
            lab = ttk.Label(row2, text="Lap %d" % (i + 1))
            v = tk.StringVar(value="0:00.01")
            ent = ttk.Entry(row2, textvariable=v, width=8)
            lab.pack(side="left")
            ent.pack(side="left", padx=(3, 10))
            self.b_times.append(v)
            self.b_time_widgets.append((lab, ent))

        top = ttk.Frame(f)
        top.pack(side="top", fill="both", expand=True)
        left = ttk.Frame(top)
        left.pack(side="left", fill="both", expand=True)
        lt = ttk.Frame(left)
        lt.pack(fill="x")
        ttk.Label(lt, text="Speed class:").pack(side="left")
        self.rec_class = tk.StringVar(value=SPEED_CLASSES[0])
        cb = ttk.Combobox(lt, textvariable=self.rec_class, values=SPEED_CLASSES, width=32, state="readonly")
        cb.pack(side="left", padx=(4, 14))
        cb.bind("<<ComboboxSelected>>", lambda e: (self.refresh_records(), self.show_record()))
        self.only_played = tk.BooleanVar(value=False)
        ttk.Checkbutton(lt, text="Only tracks with a record in this class", variable=self.only_played,
                        command=self.refresh_records).pack(side="left")
        fr, self.rec_tree = self._tree(left, ("Track", "Game, class", "Top 5", "Laps", "Best lap", "Total", "Ship",
                                              "Pilot"),
                                       (160, 190, 45, 40, 70, 70, 90, 60), height=10, select="extended")
        fr.pack(fill="both", expand=True, pady=4)
        self.rec_tree.bind("<<TreeviewSelect>>", lambda e: self.show_record())

        ed = ttk.LabelFrame(top, text="Edit position 1 (fastest) of this class", style="Game.TLabelframe",
                            padding=10)
        ed.pack(side="left", fill="y", padx=(10, 0))
        self.rec_title = ttk.Label(ed, text="Pick a track", font=("Segoe UI", 10, "bold"))
        self.rec_title.grid(row=0, column=0, columnspan=6, sticky="w")
        for c in (0, 3):
            ttk.Label(ed, text="Lap").grid(row=1, column=c, sticky="w")
            ttk.Label(ed, text="Time").grid(row=1, column=c + 1, sticky="w")
            ttk.Label(ed, text="Pads").grid(row=1, column=c + 2, sticky="w")
        self.lap_rows = []
        for i in range(10):
            row, c = 2 + i % 5, (i // 5) * 3
            ttk.Label(ed, text="%d" % (i + 1)).grid(row=row, column=c, sticky="w", pady=1)
            tv, sv = tk.StringVar(), tk.StringVar()
            ttk.Entry(ed, textvariable=tv, width=9).grid(row=row, column=c + 1, padx=(2, 2))
            ttk.Entry(ed, textvariable=sv, width=5).grid(row=row, column=c + 2, padx=(0, 12))
            self.lap_rows.append((tv, sv))
        ttk.Label(ed, text="Ship").grid(row=7, column=0, sticky="w", pady=(8, 0))
        self.rec_ship = tk.StringVar()
        ttk.Combobox(ed, textvariable=self.rec_ship, values=SHIPS, width=15).grid(
            row=7, column=1, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(ed, text="Pilot").grid(row=8, column=0, sticky="w")
        self.rec_pilot = tk.StringVar()
        ttk.Entry(ed, textvariable=self.rec_pilot, width=10).grid(row=8, column=1, columnspan=3, sticky="w")
        self.rec_total = ttk.Label(ed, text="", style="Sub.TLabel", wraplength=330)
        self.rec_total.grid(row=9, column=0, columnspan=6, sticky="w", pady=4)
        bf = ttk.Frame(ed)
        bf.grid(row=10, column=0, columnspan=6, sticky="w")
        ttk.Button(bf, text="Apply", command=self.apply_record).pack(side="left")
        ttk.Button(bf, text="Scale laps...", command=self.scale_record).pack(side="left", padx=4)
        ttk.Button(bf, text="Clear", command=self.clear_record).pack(side="left")
        ttk.Label(ed, style="Sub.TLabel", text="Times as m:ss.cc. Empty time = no lap.").grid(
            row=11, column=0, columnspan=6, sticky="w", pady=(4, 0))
        self._bulk_laps()

    def _bulk_laps(self):
        try:
            n = max(1, min(10, int(self.b_laps.get())))
        except (tk.TclError, ValueError):
            return
        if getattr(self, "b_auto", None) is not None and self.b_auto.get():
            n = 10
        for i, (lab, ent) in enumerate(self.b_time_widgets):
            st = "normal" if i < n else "disabled"
            ent.config(state=st)
            lab.config(foreground="" if i < n else "#aaa")

    def target_tracks(self, scope):
        blocks = self.save.track_blocks()
        if scope in ("all", "All tracks (incl. Zone 1-4)"):
            return list(blocks)
        if scope == "Race tracks (no Zone 1-4)":
            return [b for b in blocks if b.name not in ZONE_TRACKS]
        if scope == "HD / Fury tracks":
            return [b for b in blocks if b.name not in TRACKS_2048]
        if scope == "Zone 1-4":
            return [b for b in blocks if b.name in ZONE_TRACKS]
        if scope == "2048 tracks":
            return [b for b in blocks if b.name in TRACKS_2048]
        if scope == "Selected tracks":
            sel = set(self.rec_tree.selection())
            return [b for b in blocks if b.name in sel]
        if scope == "Tracks with a record":
            return [b for b in blocks if self.save.any_record(b)]
        return []

    @staticmethod
    def class_indexes(choice):
        return list(range(len(SPEED_CLASSES))) if choice == ALL_CLASSES else [SPEED_CLASSES.index(choice)]

    def apply_records_to(self, blocks, laps, ship, pilot, classes=None, push=True, auto=None):
        """laps: fixed list of (pads, cs); or auto=(pads, [lap times]) to use each class's lap count
        (HD: Venom 3, Flash 4, Rapier 4, Phantom 5; 2048: C 3, B 4, A 5, A+ 6), repeating the last time."""
        if not blocks:
            raise ValueError("No tracks to apply to.")
        if len(pilot.encode("latin1")) > 7:
            raise ValueError("Player name: max 7 characters")
        classes = range(len(SPEED_CLASSES)) if classes is None else classes
        for b in blocks:
            for c in classes:
                if auto:
                    pads, times = auto
                    n = class_laps(b.name, c)
                    use = [(pads, times[min(i, len(times) - 1)]) for i in range(n)]
                else:
                    use = laps
                self.save.write_record(b, use, ship, pilot, cls=c, pos=0, push=push)
        return len(blocks)

    def bulk_apply(self):
        if not self.require():
            return
        try:
            pads = int(self.b_pads.get() or "0")
            blocks = self.target_tracks(self.b_scope.get())
            classes = self.class_indexes(self.b_class.get())
            if self.b_auto.get():
                times = [parse_time(v.get()) for v in self.b_times]
                count = self.apply_records_to(blocks, None, self.b_ship.get(),
                                              self.b_pilot.get().strip() or "Player",
                                              classes=classes, push=self.b_push.get(), auto=(pads, times))
                lap_txt = "class lap counts"
            else:
                n = max(1, min(10, int(self.b_laps.get())))
                laps = [(pads, parse_time(self.b_times[i].get())) for i in range(n)]
                count = self.apply_records_to(blocks, laps, self.b_ship.get(),
                                              self.b_pilot.get().strip() or "Player",
                                              classes=classes, push=self.b_push.get())
                lap_txt = "%d laps" % n
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.after_change("Records written on %d tracks, %s (%s, %s)." % (
            count, self.b_class.get(), self.b_ship.get(), lap_txt))

    def refresh_records(self):
        tree = self.rec_tree
        sel = tree.selection()
        tree.delete(*tree.get_children())
        if not self.save:
            return
        cls = SPEED_CLASSES.index(self.rec_class.get())
        for b in self.save.track_blocks():
            r = self.save.track_record(b, cls, 0)
            if self.only_played.get() and not r["valid"]:
                continue
            tree.insert("", "end", iid=b.name, values=(TRACKS[b.name],
                                                        "%s, %s" % (track_game(b.name), class_name(b.name, cls)),
                                                        "%d/5" % self.save.class_entries(b, cls), len(r["valid"]),
                                                        fmt_time(r["best"]), fmt_time(r["total"]), r["ship"],
                                                        r["pilot"]))
        for s in sel:
            if tree.exists(s):
                tree.selection_add(s)

    def current_track(self):
        sel = self.rec_tree.selection()
        return self.save.by_name.get(sel[0]) if sel and self.save else None

    def show_record(self):
        b = self.current_track()
        if not b:
            return
        cls = SPEED_CLASSES.index(self.rec_class.get())
        r = self.save.track_record(b, cls, 0)
        self.rec_title.config(text="%s - %s (%d laps)" % (TRACKS[b.name], class_name(b.name, cls),
                                                           class_laps(b.name, cls)))
        for (tv, sv), lap in zip(self.lap_rows, r["laps"]):
            ok = lap["time"] != 0xFFFF
            tv.set(fmt_time(lap["time"]) if ok else "")
            sv.set(str(lap["stat"]) if ok else "")
        self.rec_ship.set(r["ship"] or self.b_ship.get())
        self.rec_pilot.set(r["pilot"] or self.b_pilot.get())
        self.rec_total.config(text="Total %s, best lap %s, %d speed pads" % (
            fmt_time(r["total"]), fmt_time(r["best"]), r["pads"])
                              if r["valid"] else "No record yet: fill in lap times and Apply.")

    def apply_record(self):
        b = self.current_track()
        if not b:
            return
        try:
            laps = []
            gap = False
            for i, (tv, sv) in enumerate(self.lap_rows):
                t = tv.get().strip()
                if not t:
                    gap = True
                    continue
                if gap:
                    raise ValueError("Fill the laps in order (lap %d comes after an empty lap)." % (i + 1))
                laps.append((int(sv.get().strip() or "1"), parse_time(t)))
            if not laps:
                raise ValueError("Enter at least one lap time, or use Clear.")
            self.save.write_record(b, laps, self.rec_ship.get().strip(), self.rec_pilot.get().strip(),
                                   cls=SPEED_CLASSES.index(self.rec_class.get()), pos=0)
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.after_record_edit(b)

    def clear_record(self):
        b = self.current_track()
        if b and messagebox.askyesno(APP_TITLE, "Remove the fastest %s record on %s?\n(The slower ones move up.)"
                                     % (self.rec_class.get(), TRACKS[b.name])):
            self.save.write_record(b, [], "", "", cls=SPEED_CLASSES.index(self.rec_class.get()), pos=0)
            self.after_record_edit(b)

    def scale_record(self):
        b = self.current_track()
        if not b:
            return
        r = self.save.track_record(b, SPEED_CLASSES.index(self.rec_class.get()), 0)
        if not r["valid"]:
            return
        v = self.ask("Scale lap times", "Multiply every lap time by this factor\n(0.9 = 10% faster):", "0.95")
        if not v:
            return
        try:
            f = float(v)
            if not 0.001 <= f <= 10:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_TITLE, "Enter a factor between 0.001 and 10.")
            return
        for lap in r["valid"]:
            self.save.set(lap["off"] + 4, "u16", max(1, min(65534, round(lap["time"] * f))))
        self.after_record_edit(b)

    def after_record_edit(self, b):
        self.refresh_all()
        if self.rec_tree.exists(b.name):
            self.rec_tree.selection_set(b.name)
            self.rec_tree.see(b.name)
        self.show_record()
        self.status("%s updated (not saved yet)." % TRACKS[b.name])

    # -- ships ---------------------------------------------------------------------------------
    def _build_ships(self, f):
        bar = ttk.LabelFrame(f, text="Max out ships", style="Game.TLabelframe", padding=10)
        bar.pack(side="bottom", fill="x", pady=(8, 0))
        r1 = ttk.Frame(bar)
        r1.pack(fill="x")
        ttk.Label(r1, text="Loyalty").pack(side="left")
        self.max_loy = tk.StringVar(value=str(DEFAULT_MAX_LOYALTY))
        ttk.Entry(r1, textvariable=self.max_loy, width=9).pack(side="left", padx=(4, 14))
        ttk.Label(r1, text="Laps").pack(side="left")
        self.max_laps = tk.StringVar(value="1000")
        ttk.Entry(r1, textvariable=self.max_laps, width=9).pack(side="left", padx=(4, 14))
        ttk.Label(r1, text="Time raced (h)").pack(side="left")
        self.max_hours = tk.StringVar(value="10")
        ttk.Entry(r1, textvariable=self.max_hours, width=6).pack(side="left", padx=(4, 14))
        self.max_only_up = tk.BooleanVar(value=True)
        ttk.Checkbutton(r1, text="Never lower a value", variable=self.max_only_up).pack(side="left")
        r2 = ttk.Frame(bar)
        r2.pack(fill="x", pady=(8, 0))
        ttk.Button(r2, text="Max loyalty: selected ships", command=lambda: self.ships_max(False, True)).pack(side="left")
        ttk.Button(r2, text="Max loyalty: all ships", command=lambda: self.ships_max(True, True)).pack(
            side="left", padx=6)
        ttk.Separator(r2, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(r2, text="Max loyalty + laps + time: selected", command=lambda: self.ships_max(False, False)).pack(
            side="left")
        ttk.Button(r2, text="Max loyalty + laps + time: all ships", command=lambda: self.ships_max(True, False)).pack(
            side="left", padx=6)
        ttk.Label(bar, style="Sub.TLabel", wraplength=1000, justify="left", text=(
            "Loyalty rewards: liveries at 4,000 and 8,000 points per team, trophy at 500 per team (the Van-Uber "
            "team is not counted). The field holds up to 4,294,967,295. Time raced and laps feed the Statistics "
            "screen bars only.")).pack(anchor="w", pady=(6, 0))

        cols = ("Ship", "Time raced", "Laps", "Loyalty", "Rewards", "Unknown", "Reserved")
        fr, self.ship_tree = self._tree(f, cols, (130, 110, 70, 90, 110, 80, 70), select="extended")
        fr.pack(fill="both", expand=True)
        self.ship_tree.bind("<Double-1>", self.edit_ship)
        ttk.Label(f, style="Sub.TLabel", text="Fields as the game's Statistics screen uses them (time per team, "
                  "laps per team, loyalty). Double-click a value to edit; Ctrl/Shift-click to select several ships."
                  ).pack(anchor="w", pady=4)

    def refresh_ships(self):
        tree = self.ship_tree
        sel = tree.selection()
        tree.delete(*tree.get_children())
        if not self.save:
            return
        for name, b, v in self.save.ships():
            loy = v[SHIP_LOYALTY]
            rew = "both liveries" if loy >= 8000 else "1st livery" if loy >= 4000 else "-"
            tree.insert("", "end", iid=name, values=(name, fmt_seconds(v[SHIP_TIME] // 100), v[SHIP_LAPS], loy, rew,
                                                     v[3], v[4]))
        for s in sel:
            if tree.exists(s):
                tree.selection_add(s)

    def ships_max(self, all_ships, loyalty_only):
        if not self.require():
            return
        try:
            loy = int(self.max_loy.get())
            laps = int(self.max_laps.get())
            cs = int(round(float(self.max_hours.get()) * 360000))
            for n in (loy, laps, cs):
                if not 0 <= n <= U32_MAX:
                    raise ValueError
        except ValueError:
            messagebox.showerror(APP_TITLE, "Enter whole numbers (hours may have decimals), within 0-4,294,967,295.")
            return
        names = [n for n, b, v in self.save.ships()] if all_ships else list(self.ship_tree.selection())
        if not names:
            messagebox.showinfo(APP_TITLE, "Select one or more ships first (or use the 'all ships' button).")
            return
        targets = {SHIP_LOYALTY: loy} if loyalty_only else {SHIP_LOYALTY: loy, SHIP_LAPS: laps, SHIP_TIME: cs}
        for n in names:
            b = self.save.by_name[n]
            for i, val in targets.items():
                o = b.data + 4 * i
                if self.max_only_up.get() and self.save.get(o) >= val:
                    continue
                self.save.set(o, "u32", val)
        self.after_change("%s set on %d ships." % ("Loyalty" if loyalty_only else "Loyalty, laps and time",
                                                   len(names)))

    def edit_ship(self, event):
        row = self.ship_tree.identify_row(event.y)
        col = self.ship_tree.identify_column(event.x)
        if not row or col in ("", "#1"):
            return
        i = {2: 0, 3: 1, 4: 2, 6: 3, 7: 4}.get(int(col[1:]))
        if i is None:
            return
        b = self.save.by_name[row]
        v = self.ask("Edit ship", "%s: %s" % (row, SHIP_FIELDS[i]), str(self.save.get(b.data + 4 * i)))
        if v is None:
            return
        try:
            n = int(v)
            if not 0 <= n <= 0xFFFFFFFF:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_TITLE, "Enter a whole number (0 or more).")
            return
        self.save.set(b.data + 4 * i, "u32", n)
        self.after_change("%s updated." % row)

    # -- header stats ------------------------------------------------------------------------------
    def _build_stats(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.stats_nonzero = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Hide fields that are 0", variable=self.stats_nonzero,
                        command=self.refresh_stats).pack(side="left")
        xp = ttk.Frame(top)
        xp.pack(side="right")
        ttk.Label(xp, text="2048 XP").pack(side="left")
        self.xp_var = tk.StringVar()
        ttk.Entry(xp, textvariable=self.xp_var, width=10).pack(side="left", padx=4)
        ttk.Button(xp, text="Set XP", command=self.on_set_xp).pack(side="left")
        self.rank_var = tk.StringVar()
        ttk.Combobox(xp, textvariable=self.rank_var, values=[str(i) for i in range(1, 51)], width=4,
                     state="readonly").pack(side="left", padx=(12, 4))
        ttk.Button(xp, text="Set rank", command=self.on_set_rank).pack(side="left")
        self.stats_known = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Only named fields", variable=self.stats_known,
                        command=self.refresh_stats).pack(side="left", padx=10)
        fr, self.stat_tree = self._tree(f, ("ID", "Offset", "Stat", "Value", "Shown as", "Type / max", "Note"),
                                        (45, 65, 260, 110, 110, 170, 200))
        fr.pack(fill="both", expand=True, pady=4)
        self.stat_tree.bind("<Double-1>", self.edit_stat)
        ttk.Label(f, style="Sub.TLabel", wraplength=1000, justify="left", text=(
            "The profile header is the game's statistics array: offset = 4 + 4 x ID. Names come from the game's own "
            "stat list and Statistics screen; times are in seconds, distances in km. Whole-number stats hold up to "
            "4,294,967,295; decimal stats up to about 3.4 x 10^38. 'Unknown' fields are not shown anywhere the "
            "game explains; leave them alone unless you know better. Double-click to edit.")).pack(anchor="w")

    def refresh_stats(self):
        tree = self.stat_tree
        tree.delete(*tree.get_children())
        if not self.save:
            return
        self.xp_var.set(str(self.save.get(XP_OFFSET)))
        for off, v in self.save.header_fields():
            sid, name, typ, unit, note = stat_info(off)
            if self.stats_nonzero.get() and v == 0:
                continue
            if self.stats_known.get() and name.startswith("Unknown"):
                continue
            if typ == F:
                val = struct.unpack("<f", struct.pack("<I", v))[0]
                shown = "%.3f %s" % (val, unit)
                tmax = "decimal, max ~3.4e38"
                vtxt = "%.3f" % val
            else:
                vtxt = str(v)
                shown = fmt_seconds(v) if unit == "s" else ("rank %d" % rank_for_xp(v) if unit == "XP" else "")
                tmax = "whole, max 4,294,967,295"
            tree.insert("", "end", iid=str(off), values=(
                "" if sid is None else sid, "0x%03X" % off, name, vtxt, shown, tmax, note))

    def on_set_xp(self):
        if not self.require():
            return
        try:
            v = int(self.xp_var.get())
            if not 0 <= v <= 0xFFFFFFFF:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_TITLE, "XP must be a whole number.")
            return
        self.save.set(XP_OFFSET, "u32", v)
        self.after_change("XP set to %d (rank %d)." % (v, rank_for_xp(v)))

    def on_set_rank(self):
        if self.rank_var.get():
            self.xp_var.set(str(XP_RANKS[int(self.rank_var.get()) - 1]))
            self.on_set_xp()

    def edit_stat(self, event):
        row = self.stat_tree.identify_row(event.y)
        if not row:
            return
        off = int(row)
        v = self.save.get(off)
        sid, label, typ, unit, note = stat_info(off)
        is_f = typ == F
        cur = "%.3f" % self.save.get(off, "f32") if is_f else str(v)
        hint = "decimal number" if is_f else "whole number, 0 - 4,294,967,295"
        if unit == "s":
            hint += ", in seconds (3600 = 1 hour)"
        if unit == "km":
            hint += ", in km"
        nv = self.ask("Edit stat", "%s\n(%s)" % (label, hint), cur)
        if nv is None:
            return
        try:
            if is_f:
                self.save.set(off, "f32", float(nv))
            else:
                n = int(nv)
                if not 0 <= n <= 0xFFFFFFFF:
                    raise ValueError
                self.save.set(off, "u32", n)
        except (ValueError, struct.error):
            messagebox.showerror(APP_TITLE, "That is not a valid number.")
            return
        self.after_change("%s updated." % label)

    # -- settings ----------------------------------------------------------------------------------
    def _build_settings(self, f):
        fr, self.set_tree = self._tree(f, ("Setting", "Value"), (300, 400))
        fr.pack(fill="both", expand=True)
        self.set_tree.bind("<Double-1>", self.edit_setting)

    def refresh_settings(self):
        tree = self.set_tree
        tree.delete(*tree.get_children())
        if not self.save:
            return
        for b, val in self.save.settings():
            tree.insert("", "end", iid=str(b.hdr), values=(b.name, val))

    def edit_setting(self, event):
        row = self.set_tree.identify_row(event.y)
        if not row:
            return
        b = next(x for x in self.save.blocks if x.hdr == int(row))
        cur = self.save.get_str(b.data, 0x40)
        v = self.ask("Edit setting", b.name, cur, SETTING_CHOICES.get(cur))
        if v is None:
            return
        try:
            if not all(32 <= ord(c) < 127 for c in v):
                raise ValueError("Use plain text only")
            self.save.set_str(b.data, 0x40, v)
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.after_change("%s updated." % b.name)

    # -- raw ---------------------------------------------------------------------------------------
    def _build_raw(self, f):
        left = ttk.Frame(f)
        left.pack(side="left", fill="y")
        self.raw_filter = tk.StringVar()
        ent = ttk.Entry(left, textvariable=self.raw_filter)
        ent.pack(fill="x")
        ent.bind("<KeyRelease>", lambda e: self.refresh_raw())
        fr, self.raw_tree = self._tree(left, ("Block", "Offset", "Size"), (220, 80, 60), height=24)
        fr.pack(fill="both", expand=True, pady=4)
        self.raw_tree.bind("<<TreeviewSelect>>", lambda e: self.show_raw())
        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.raw_text = tk.Text(right, font=("Consolas", 10), wrap="none")
        self.raw_text.pack(fill="both", expand=True)
        form = ttk.Frame(right, padding=(0, 6))
        form.pack(fill="x")
        ttk.Label(form, text="Offset in block (hex):").pack(side="left")
        self.raw_off = tk.StringVar(value="0")
        ttk.Entry(form, textvariable=self.raw_off, width=8).pack(side="left", padx=4)
        self.raw_type = tk.StringVar(value="u32")
        ttk.Combobox(form, textvariable=self.raw_type, values=["u8", "u16", "u32", "i32", "f32"], width=5,
                     state="readonly").pack(side="left")
        ttk.Button(form, text="Read", command=self.raw_read).pack(side="left", padx=4)
        self.raw_val = tk.StringVar()
        ttk.Entry(form, textvariable=self.raw_val, width=16).pack(side="left", padx=4)
        ttk.Button(form, text="Write", command=self.raw_write).pack(side="left")

    def refresh_raw(self):
        tree = self.raw_tree
        sel = tree.selection()
        tree.delete(*tree.get_children())
        if not self.save:
            return
        flt = self.raw_filter.get().lower()
        tree.insert("", "end", iid="header", values=("(profile header)", "0x0", "0x%X" % HEADER_END))
        for b in self.save.blocks:
            if flt and flt not in b.name.lower():
                continue
            tree.insert("", "end", iid=str(b.hdr), values=(b.name, "0x%X" % b.hdr, "0x%X" % b.size))
        for s in sel:
            if tree.exists(s):
                tree.selection_set(s)
                tree.see(s)

    def raw_block(self):
        sel = self.raw_tree.selection()
        if not sel or not self.save:
            return None
        if sel[0] == "header":
            return 0, HEADER_END, "(profile header)"
        b = next(x for x in self.save.blocks if x.hdr == int(sel[0]))
        return b.data, b.size, b.name

    def show_raw(self):
        rb = self.raw_block()
        if not rb:
            return
        start, size, name = rb
        d = self.save.buf
        n = min(size, 0x1000)
        lines = ["%s: %d bytes at file offset 0x%X%s" % (name, size, start,
                                                         " (first 4 KB shown)" if size > n else ""), ""]
        for o in range(0, n, 16):
            row = bytes(d[start + o:start + min(o + 16, n)])
            lines.append("%05X  %-48s %s" % (o, row.hex(" "), "".join(chr(c) if 32 <= c < 127 else "." for c in row)))
        self.raw_text.config(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("end", "\n".join(lines))
        self.raw_text.config(state="disabled")

    def raw_target(self):
        rb = self.raw_block()
        if not rb:
            raise ValueError("Pick a block on the left first.")
        start, size, _ = rb
        off = int(self.raw_off.get(), 16)
        typ = self.raw_type.get()
        width = struct.calcsize(SaveFile.FMT[typ])
        if not 0 <= off <= size - width:
            raise ValueError("Offset is outside this block (size 0x%X)." % size)
        return start + off, typ

    def raw_read(self):
        try:
            pos, typ = self.raw_target()
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        v = self.save.get(pos, typ)
        self.raw_val.set(("%.6g" % v) if typ == "f32" else str(v))

    def raw_write(self):
        try:
            pos, typ = self.raw_target()
            txt = self.raw_val.get().strip()
            self.save.set(pos, typ, float(txt) if typ == "f32" else int(txt, 0))
        except (ValueError, struct.error) as e:
            messagebox.showerror(APP_TITLE, "Cannot write: %s" % e)
            return
        self.refresh_all()
        self.show_raw()
        self.status("Wrote %s at file offset 0x%X (not saved yet)." % (typ, pos))

    # -- helpers ---------------------------------------------------------------------------------
    def status(self, text):
        self.status_var.set(text)

    def mark(self):
        dirty = self.save is not None and self.save.dirty
        self.title(APP_TITLE + ("  *unsaved changes*" if dirty else ""))

    def require(self):
        if not self.save:
            messagebox.showinfo(APP_TITLE, "Open a save first.")
            return False
        return True

    def ask(self, title, prompt, initial, choices=None):
        return EditDialog(self, title, prompt, initial, choices).result

    # -- load / save -------------------------------------------------------------------------------
    def load(self, path):
        try:
            self.save = SaveFile(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "Could not read %s:\n%s" % (path, e))
            return
        self.path_lbl.config(text=path)
        save_prefs(last_save=os.path.abspath(path))
        pilot = self.save.default_pilot()
        self.q_pilot.set(pilot)
        self.b_pilot.set(pilot)
        self.refresh_all()
        self.status("Loaded %s (%d blocks)." % (os.path.basename(os.path.dirname(path)), len(self.save.blocks)))

    def refresh_all(self):
        self.refresh_quick()
        self.refresh_campaign()
        self.refresh_c2048()
        self.refresh_records()
        self.refresh_ships()
        self.refresh_stats()
        self.refresh_settings()
        self.refresh_raw()
        self.mark()

    def confirm_discard(self):
        if self.save and self.save.dirty:
            return messagebox.askyesno(APP_TITLE, "Discard the changes you have not saved?")
        return True

    def on_open(self):
        if not self.confirm_discard():
            return
        start = os.path.dirname(self.save.path) if self.save else None
        p = filedialog.askopenfilename(title="Open DATA.BIN", initialdir=start,
                                       filetypes=[("WipEout profile", "DATA.BIN"), ("All files", "*.*")])
        if p:
            self.load(p)

    def on_new_save(self, folder=None):
        """Create a clean save. folder: chosen folder (asked with a folder picker when None)."""
        if not template_profile():
            messagebox.showerror(APP_TITLE, "The clean template save is missing from\n%s" % TEMPLATE_DIR)
            return
        if not self.confirm_discard():
            return
        if folder is None:
            start = savedata_root_of(self.save.path) if self.save else default_savedata_root()
            while start and not os.path.isdir(start):
                start = os.path.dirname(start) if os.path.dirname(start) != start else None
            folder = filedialog.askdirectory(
                parent=self, initialdir=start or os.path.expanduser("~"), mustexist=True,
                title="Where should the new save go? Pick the game folder, its 'user' folder or a 'savedata' folder")
            if not folder:
                return
        root = resolve_savedata_root(folder)
        user_id = pick_user_id(root)
        name = os.path.basename(template_profile()[0])
        target = os.path.join(root, user_id, TITLE_ID, name, "DATA.BIN")
        exists = os.path.isfile(target)
        backups = os.path.join(os.path.dirname(root), "savedata_editor_backups")
        msg = "Create a new, clean WipEout Omega save here?\n\n%s\n\n" % os.path.dirname(target)
        if exists:
            msg += "A save already exists there. It is backed up to\n%s\nfirst, then replaced.\n\n" % backups
        if os.path.basename(root).lower() != "savedata":
            msg += ("Note: the game normally keeps its saves in <game folder>\\user\\savedata. The folder you "
                    "picked does not look like that.\n\n")
        msg += "Continue?"
        if not messagebox.askyesno(APP_TITLE, msg, icon="warning" if exists else "question"):
            return
        if game_running() and not messagebox.askyesno(
                APP_TITLE, "The game seems to be running. Close it first, or it may overwrite the new save.\n\n"
                           "Continue anyway?"):
            return
        try:
            path = create_new_save(root, user_id, backups)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "Could not create the save:\n%s" % e)
            return
        self.load(path)
        self.status("New clean save created: %s" % path)

    def on_reload(self):
        if self.save and self.confirm_discard():
            self.load(self.save.path)

    def snapshot_root(self):
        return os.path.join(user_dir_for(self.save.path), "savedata_snapshots")

    def backup_root(self):
        # Separate from the launcher's savedata_backups, which it prunes to its 10 newest.
        return os.path.join(user_dir_for(self.save.path), "savedata_editor_backups")

    def on_save(self):
        if not self.require():
            return
        if not self.save.dirty:
            self.status("Nothing to save.")
            return
        if game_running() and not messagebox.askyesno(
                APP_TITLE, "The game seems to be running. It may overwrite your edits when it saves.\n\nSave anyway?"):
            return
        try:
            os.makedirs(self.backup_root(), exist_ok=True)
            backup = self.save.save(self.backup_root())
        except Exception as e:
            messagebox.showerror(APP_TITLE, "Saving failed:\n%s" % e)
            return
        self.mark()
        self.status("Saved. Backup of the previous version: %s" % backup)

    def on_snapshot(self):
        if not self.require():
            return
        name = self.ask("Save snapshot", "Name for this snapshot of your progress\n"
                        "(the version on disk is saved, not unsaved edits):", "my progress")
        if not name:
            return
        tag = re.sub(r"[^\w\- ]", "_", name).strip() or "snapshot"
        try:
            dest = backup_profile(self.save.path, self.snapshot_root(), tag)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "Snapshot failed:\n%s" % e)
            return
        self.status("Snapshot saved to %s" % dest)

    def on_restore(self):
        if not self.require() or not self.confirm_discard():
            return
        _, _, rel = profile_dirs(self.save.path)
        user = user_dir_for(self.save.path)
        cands = []
        for root, kind in ((self.snapshot_root(), "snapshot"), (self.backup_root(), "editor backup"),
                           (os.path.join(user, "savedata_backups"), "launcher backup")):
            if os.path.isdir(root):
                for n in os.listdir(root):
                    if os.path.isfile(os.path.join(root, n, rel, "DATA.BIN")):
                        cands.append((n, kind, os.path.join(root, n)))
        if not cands:
            messagebox.showinfo(APP_TITLE, "No snapshots or backups found for this profile.")
            return
        cands.sort(key=lambda c: c[0], reverse=True)
        win = tk.Toplevel(self)
        win.title("Restore")
        win.geometry("560x400")
        win.transient(self)
        ttk.Label(win, text="Pick a version to restore. Your current save is backed up first.",
                  padding=8).pack(anchor="w")
        fr, tree = self._tree(win, ("Name", "Type"), (400, 110), height=12)
        fr.pack(fill="both", expand=True, padx=8)
        for i, (n, k, p) in enumerate(cands):
            tree.insert("", "end", iid=str(i), values=(n, k))

        def do():
            sel = tree.selection()
            if not sel:
                return
            n, k, src = cands[int(sel[0])]
            if game_running() and not messagebox.askyesno(APP_TITLE, "The game seems to be running. Restore anyway?",
                                                          parent=win):
                return
            prof, sce, _ = profile_dirs(self.save.path)
            try:
                backup = backup_profile(self.save.path, self.backup_root(), "before-restore")
                shutil.copy2(os.path.join(src, rel, "DATA.BIN"), os.path.join(prof, "DATA.BIN"))
                sp = os.path.join(src, rel + ".sce_sys")
                if sce and os.path.isdir(sp):
                    for fn in os.listdir(sp):
                        shutil.copy2(os.path.join(sp, fn), os.path.join(sce, fn))
            except Exception as e:
                messagebox.showerror(APP_TITLE, "Restore failed:\n%s" % e, parent=win)
                return
            win.destroy()
            self.load(self.save.path)
            self.status("Restored '%s'. Previous save backed up to %s" % (n, backup))

        ttk.Button(win, text="Restore selected", command=do).pack(pady=8)

    def on_close(self):
        if self.confirm_discard():
            self.destroy()


def main():
    try:  # sharp text on high-DPI screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    path = sys.argv[1] if len(sys.argv) > 1 else None
    App(path).mainloop()


if __name__ == "__main__":
    main()
