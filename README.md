# WipEout Omega Collection PC Save Editor

By **Synapz3**. Released under the [MIT License](LICENSE).

A Windows GUI for the WipEout Omega Collection save used by the `wipeout_pc` port
(`user\savedata\1\CUSA05670\PROFILExx\DATA.BIN`).

> **Compatible with [wipeout_pc](https://github.com/abdelmoumendeghbouche/wipeout_pc)**, the native PC port of
> WipEout Omega Collection. The editor reads and writes the saves that port creates in its `user\savedata`
> folder, and it was developed and tested against it (WipEout Omega Collection CUSA05670, update 01.07).
> Put the `save-editor` folder inside or next to the wipeout_pc folder and the save is found automatically.
> This editor is a separate project: it is not part of wipeout_pc and not made by its authors.

Start it with **`WipEout Save Editor.bat`** (or `python wipeout_save_editor.py [path\to\DATA.BIN]`).
It finds the save next to the port automatically (or the last one you opened). Needs Python 3.7+ with Tkinter: see *Requirements and installation* below.

**Close the game before saving.** The game rewrites the whole file when it saves.

## Requirements and installation

| Dependency | Version | Needed for | How to get it |
|---|---|---|---|
| [Python](https://www.python.org/downloads/) | 3.7 or newer (tested with 3.14.3) | running the editor | python.org installer, or `winget install Python.Python.3.13` |
| Tkinter (Tcl/Tk) | 8.6 (comes with Python) | the window/GUI | included in the python.org Windows installer ("tcl/tk and IDLE", ticked by default); Linux: `sudo apt install python3-tk` |
| Python standard library | (built in) | everything else: `os`, `re`, `shutil`, `struct`, `subprocess`, `sys`, `datetime`, `json`, `ctypes` | nothing to install |

There are **no third-party packages** and nothing to install with pip (`requirements.txt` documents this).

Optional, not needed by the editor: the game itself (WipEout Omega Collection running in the `wipeout_pc` port
or a PS4 emulator with shadPS4-style `savedata`) to use the saves you edit.

### Install on Windows

1. Download Python 3 from https://www.python.org/downloads/windows/ and run the installer.
   Tick **"Add python.exe to PATH"**; keep **"tcl/tk and IDLE"** ticked (it is by default).
   Or, in a terminal: `winget install Python.Python.3.13`
2. Check it: open a terminal and run
   `python -c "import tkinter; print(tkinter.TkVersion)"` - it should print `8.6`.
3. Copy the whole `save-editor` folder (including `template\`) anywhere, ideally next to or inside the
   game folder so the save is found automatically.
4. Double-click **`WipEout Save Editor.bat`**. If Python is missing it tells you what to install.

### Linux / macOS

`python3 wipeout_save_editor.py [path/to/DATA.BIN]` (install `python3-tk` on Debian/Ubuntu). The editor itself
is platform independent; only the "is the game running?" check uses Windows' `tasklist` and is skipped elsewhere.

## Layout

- **Top bar (FILE)**: open, **New save...**, reload, **Save changes**, snapshots and restore.
- **Left menu**: the pages below.

| Page | What you can do |
|---|---|
| Quick actions | Per game: **HD** and **HD Fury** "max rank" = gold at Elite on every campaign event (87 + 80 events, all campaign points); **2048** max rank 50 (1,790,000 XP); records on every track at 0:00.01 per lap with your ship and name; or all of it at once |
| HD / Fury medals | All 167 HD/Fury events (track, mode, medal, difficulty, best). Select rows, set Gold/Silver/Bronze/None on Novice/Skilled/Elite |
| 2048 campaign | All 115 WipEout 2048 events (seasons 2048/2049/2050, ship challenges, speed laps): unlock, lock, complete (pass), complete (elite, experimental), reset. Quick actions has one button that opens 2049 and 2050 |
| Track records | Best race per track: lap times (m:ss.cc), ship, pilot. Create a record on any track, clear one, scale times; **apply one ship + per-lap times to all tracks (incl. Zone 1-4) / race tracks / HD / 2048 / Zone / selected tracks** |
| Ships | Time raced, laps and loyalty per team; **max loyalty** (and optionally laps/time) for selected or all ships |
| Profile stats | All 240 profile statistics with names, units, readable values and maximum values; 2048 XP / rank |
| Settings | Game options as stored (camera, HUD, volumes, team, livery...) |
| Advanced | Every block with a hex view; read/write a u8/u16/u32/i32/f32 anywhere |

Safety: edits stay in memory until **Save changes**. Each save first copies the profile (DATA.BIN and the
`.sce_sys` folder) to `user\savedata_editor_backups\<time>-editor`. **Save snapshot...** keeps a named copy in
`user\savedata_snapshots`; **Restore...** brings back a snapshot, an editor backup or a launcher backup
(after backing up the current save). The file keeps its size; only the changed values are written.

## New save

**New save...** (also offered at start when no save is found) asks where the save should go. Pick the
game folder, its `user` folder or a `savedata` folder: the editor puts the save in
`<savedata>\<user id>\CUSA05670\PROFILE03` and shows the full path before writing. A save already there
is backed up to `savedata_editor_backups` (next to `savedata`) first. The new save is the clean file the
game writes on a first start (`template\`, keep that folder next to the script); the editor skips the
blocks a fresh save does not have yet.

## Finding saves

No fixed paths: the editor opens the save you used last (remembered in
`%APPDATA%\wipeout-save-editor\editor.json`), otherwise it looks in `user\savedata` next to or above the
editor folder, or in the current folder. **Open save...** works from anywhere.

## Not possible yet

- **2048 elite results**: 'pass' (result 1) is what the game wrote in testing; 'elite' (2) is a guess.

## Save format

- `0x000-0x3C3`: profile header = the game's statistics array (in memory at profile+0x1BC): `0x000` is
  always 0x2000, then stat N at `4 + 4*N`. Names come from the game's stat export list and the Statistics
  screen (`stats_definition.xml` StatIDs), e.g. 0 speed pads, 2 laps, 5 time in game (s), 17-19 bronze/
  silver/gold, 25 distance (km, float), 92-96 laps per class. **2048 XP = stat 199 (`0x320`)**: the
  profile screen compares profile+0x4DC with the XPRanks table and the race code adds XP awards there.
  (`0x2C0`, stat 175, grows each session and is *not* XP.) Whole-number stats hold up to 4,294,967,295.
- From `0x3C4`: a chain of blocks `u32 name hash | name[0x48] | u32 size | 0x18 bytes runtime data | data`.
  The hash depends only on the name; no checksum over the data.
- **Campaign cells** `grid<g>_<r>_<c>` (0x48 bytes), from the game's race-result code (eboot 0x2A7700) and
  points code (0x4A7200): `+0 u32 best` (position, time in 1/100 s or score; 0/0xFFFFFFFF = none),
  `+4 u32 second best` (best lap), `+8 u8 difficulty` (0 Novice, 1 Skilled, 2 Elite), `+9 u8 medal`
  (0 gold, 1 silver, 2 bronze, 0xFF none; points = 3 - medal), then 15 floats (left alone).
  Cell → track/mode/targets comes from `plugins/grids/grid_XX.xml` (HD 87 cells, Fury 80, matching the
  game's `MedalCountHD="87"` / `MedalCountFURY="80"` unlock rules).
- **Track blocks** `01_Track`..`28_Track` (HD/Fury, 25-28 = Zone 1-4, same layout), `altima`..`tower` (2048), 0x4000 bytes:
  `u32 flags` (bits 0-1 set when a lap record exists), 10 lap slots of `u32 value?, u16 time in 1/100 s
  (0xFFFF = empty), u16 0`, ship name at +0x54 (32 bytes), pilot name at +0x74 (8 bytes). The rest is
  leftover memory and is never touched.
- **Ship blocks** (0x14 bytes): 5 u32: time raced (1/100 s), laps, loyalty, unknown, 0 (the Statistics
  screen's team source IDs 0/1/2). Loyalty rewards at 4,000 and 8,000 (`plugins/teams/Definition.xml`).
- **Option blocks** (0x40 bytes): text such as `FE_ON`, `OPT_FAR`, `80%`.
- **Mjolnir** (2048 campaign): SP.xml instances, each `u32 instance id, u32 type`, then fields
  `u32 tag, u32 length, data`. Event records: tag `0x3C0` state (0 locked, 2 unlocked), `0x3F0` completed
  (u8), `0x3E8` result (1 = pass), `0x5C0` XP earned + livery + ship, `0x52C` best result. Season unlock
  chain (SP.xml `m_pEventRequired`): 2049 - Event 1 needs 2048 - Event 7, 2050 - Event 1 needs 2049 - Event 8.

## License

Copyright (c) 2026 Synapz3. The editor's code is released under the MIT License; see [LICENSE](LICENSE).

The file in `template\` is a save written by the game itself on a first start; the event, track and stat
tables in the script were read from the game's own data. WipEout is a trademark of Sony Interactive
Entertainment; this project is not affiliated with or endorsed by Sony.
