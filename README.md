# sensors2nmea

Turn a Windows laptop's built-in GNSS receiver into a network NMEA 0183 source
for OpenCPN, Navionics, qtVlm or any other chart plotter — no serial cable, no
virtual COM port, no driver hacks.

Many modern laptops carry a GNSS receiver inside their WWAN (mobile broadband)
module. Windows exposes it through the Sensors and Geolocation APIs, but chart
plotters expect NMEA 0183 on a serial port or TCP socket. `sensors2nmea` bridges
the two: it reads `Windows.Devices.Geolocation` / `Windows.Devices.Sensors` and
broadcasts standard NMEA sentences over TCP to every connected client.

It can also **replay a recorded GPX track** instead of reading the sensors,
which is useful for testing a plotter setup at your desk.

```
┌──────────────────────┐      ┌──────────────┐      ┌──────────────────┐
│ Windows Sensors API  │      │              │      │ OpenCPN          │
│  Geolocator (GNSS)   │─────▶│ sensors2nmea │─────▶│ Navionics        │
│  Compass             │      │   TCP :10110 │      │ qtVlm            │
│  Inclinometer        │      │              │      │ ...many clients  │
└──────────────────────┘      └──────────────┘      └──────────────────┘
         or  ──▶ GPX track playback ──▶
```

> **This is not a certified navigation device.** See [Disclaimer](#disclaimer)
> before you rely on it for anything. Seriously — read it.

---

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Connecting OpenCPN](#connecting-opencpn)
- [Command-line options](#command-line-options)
- [What the data actually means](#what-the-data-actually-means)
- [GPX track playback](#gpx-track-playback)
- [Reliability](#reliability)
- [Troubleshooting](#troubleshooting)
- [Reference setup](#reference-setup)
- [Known limitations](#known-limitations)
- [Disclaimer](#disclaimer)
- [License and attribution](#license-and-attribution)

---

## Features

- **GNSS position** → `GPGGA`, `GPRMC` with real fix quality, not invented values
- **Time from the receiver**, not the PC clock — sentences carry the actual fix
  timestamp, so one fix is transmitted exactly once
- **UTC + local zone** → `GPZDA`
- **Course and speed over ground** → `GPVTG` (many plotters prefer this over RMC)
- **Compass heading** → `HCHDM`, `HCHDT`, `HCHDG`, with mounting-offset and
  magnetic-variation correction
- **Pitch and roll** → `YXXDR`
- **GPX playback** of tracks or routes, with speed multiplier and looping
- **Honest fix quality**: a WiFi- or IP-derived position is flagged invalid
  instead of masquerading as a satellite fix
- **Multi-client TCP server** — plotter, logger and a debug `telnet` at once
- **Self-healing**: every task is supervised and restarted with backoff; sensors
  that disappear are re-acquired automatically
- **No dependencies** for `--simulate` and `--gpx` modes — those run on plain
  Python, including on Linux and macOS

## Requirements

| | |
|---|---|
| OS | Windows 10 or 11 for sensor mode; any OS for `--simulate` / `--gpx` |
| Python | 3.9 or newer |
| Hardware | a GNSS receiver Windows recognises as a location sensor |
| Permissions | Location access enabled for desktop apps |

Check whether Windows sees a usable receiver:

```powershell
Get-PnpDevice -Class Sensor | Select-Object FriendlyName, Status
```

If that lists nothing, Windows has no location sensor and only `--simulate` and
`--gpx` will work. A USB GPS dongle that appears as a **COM port** is *not* a
Windows location sensor — feed it to your plotter directly instead; you do not
need this tool for that.

## Installation

### From PyPI (recommended)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install "sensors2nmea[sensors]"
```

This installs a `sensors2nmea` command on your PATH. The `[sensors]` extra
pulls in the WinRT bindings needed to read the real hardware.

If you only want simulation or GPX playback, drop the extra — the core has **no
dependencies at all** and runs on Linux and macOS too:

```powershell
pip install sensors2nmea
```

### From source

```powershell
git clone https://github.com/BTL00/sensors2nmea.git
cd sensors2nmea
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[sensors]"
```

### Manual, no packaging

The program is a single file. Download `sensors2nmea.py`, install the WinRT
bindings, and run it:

```powershell
pip install winrt-runtime winrt-Windows.Devices.Sensors `
            winrt-Windows.Devices.Geolocation winrt-Windows.Foundation
python sensors2nmea.py
```

The older monolithic `winsdk` package also works — the code tries `winrt-*`
first and falls back automatically.

### Enabling location access

Settings → Privacy & security → Location, and turn on both:

- **Location services**
- **Let desktop apps access your location**

Without this, `Geolocator.request_access_async()` is denied and the program says
so explicitly at startup.

## Running

```powershell
# defaults: port 10110, all sentences, 5 Hz, listening on every interface
sensors2nmea

# a specific port, and echo sentences to the console so you can see them
sensors2nmea --port 15555 -v

# bind to localhost only
sensors2nmea --host 127.0.0.1 --port 10110

# synthetic data, no sensors needed
sensors2nmea --simulate

# replay a recorded track in real time
sensors2nmea --gpx examples/sample_route.gpx --sim-as-real

# replay 10x faster, looping forever
sensors2nmea --gpx examples/sample_route.gpx --sim-as-real --gpx-speed 10 --gpx-loop
```

Stop it with `Ctrl+C`.

Verify the stream without a plotter:

```powershell
# PowerShell one-liner: dump whatever is being broadcast
$c = New-Object Net.Sockets.TcpClient('127.0.0.1', 10110)
$r = New-Object IO.StreamReader($c.GetStream())
1..10 | ForEach-Object { $r.ReadLine() }
$c.Close()
```

## Connecting OpenCPN

1. **Options → Connections → Add Connection**
2. Type: **Network**, Protocol: **TCP**
3. Address: `127.0.0.1` (same machine) or the LAN IP of the machine running it
4. Data port: `10110`, or whatever you passed to `--port`
5. Leave input filtering off until you know it works

Two things that cost real debugging time, so they are worth stating plainly:

**GPX playback needs `--sim-as-real`.** By default, replayed and simulated data
are honestly flagged as simulator output (`GGA` quality `8`, FAA mode `S`).
OpenCPN deliberately ignores such data: the ship icon will not move and the
Dashboard shows `---` for SOG and COG even though the values are present in the
sentences. Adding `--sim-as-real` presents the data as an ordinary GPS fix
(`GGA` quality `1`, mode `A`) and everything appears. Do **not** use that flag
with real sensors — there, honest source flagging matters and works correctly.

**OpenCPN ignores the local-zone fields of `GPZDA`.** Its local time comes from
its own setting, not from the NMEA stream: Dashboard preferences → *Appearance*
→ **Local offset from UTC**. Leave it at `00:00` to follow the computer's time
zone. Also note that the Dashboard has three different clocks — *GPS clock*
shows raw UTC, *Local GPS clock* applies that offset, *Local CPU clock* reads
the operating system. Pick the one you actually want.

## Command-line options

### Network and output

| Option | Default | Meaning |
|---|---|---|
| `--host ADDR` | `0.0.0.0` | Listen address. `0.0.0.0` exposes every interface |
| `--port N` | `10110` | TCP port. `10110` is the IANA port for NMEA 0183 |
| `--rate HZ` | `5` | Broadcast frequency |
| `--sentences LIST` | all | Comma-separated: `gps,zda,vtg,hdg,att` |
| `--gps-repeat` | off | Repeat the last fix at `--rate` instead of once per fix |
| `--stale SEC` | `5` | Stop sending after this long without a fresh reading |
| `-v`, `--verbose` | off | Also print sentences to stdout |
| `--status-interval S` | `60` | Status heartbeat in the log; `0` disables |
| `--log-file PATH` | – | Append diagnostics to a file as well as stderr |
| `--version` | | Print version and exit |

### Heading correction

| Option | Default | Meaning |
|---|---|---|
| `--offset DEG` | `0` | Added to the compass reading: mounting error, deviation |
| `--variation DEG` | – | Magnetic variation, east positive. Used only when Windows does not supply a true heading |

### Simulation and playback

| Option | Default | Meaning |
|---|---|---|
| `--simulate` | off | Synthetic data instead of sensors |
| `--gpx FILE` | – | Replay a GPX track or route. Implies simulation mode |
| `--gpx-speed X` | `1` | Playback rate multiplier |
| `--gpx-knots KN` | `5` | Speed used only when the GPX has no `<time>` stamps |
| `--gpx-loop` | off | Restart from the beginning at the end of the track |
| `--sim-as-real` | off | Present simulated data as a genuine GPS fix. Needed for OpenCPN |

## What the data actually means

This tool tries hard not to invent numbers. That occasionally makes the output
look emptier than other NMEA sources — deliberately.

**The satellite-count field in `GPGGA` is always empty.** Windows does not
expose how many satellites were used, nor a list of satellites in view. A number
there would be fiction, and `GPGSV` cannot be generated honestly at all.

**HDOP comes from the receiver** (`GeocoordinateSatelliteData`) or is left
empty. It is not derived from the accuracy radius by some invented factor.

**Fix quality follows the actual position source:**

| `position_source` | `GGA` | `RMC` | FAA mode | Usable for navigation |
|---|---|---|---|---|
| `SATELLITE` | 1 | A | A | yes |
| `WI_FI`, `CELLULAR`, `IP_ADDRESS` | 0 | V | N | **no** |
| `DEFAULT`, `OBFUSCATED`, `UNKNOWN` | 0 | V | N | **no** |
| `--simulate` / `--gpx` | 8 | A | S | no — simulation |
| …plus `--sim-as-real` | 1 | A | A | no — deliberately disguised |

Windows will silently fall back to WiFi or IP geolocation when satellites are
unavailable, and such a position can be tens of kilometres off. Those fixes are
marked invalid rather than passed off as GPS. Every change of source is logged:

```
[2026-10-03T22:43:31] INFO  Geolocator: zrodlo pozycji = SATELLITE (fix nawigacyjny)
[2026-10-03T22:51:07] WARN  Geolocator: zrodlo pozycji = WI_FI (NIE nadaje sie do nawigacji -> GGA 0 / RMC V)
```

**Timestamps come from the fix, not from the moment of transmission.** The
receiver reports roughly once per second while the default broadcast rate is
5 Hz, so stamping with the PC clock would send the same measurement five times
under five invented timestamps. Instead each fix is transmitted once with its
own timestamp, and a dropped fix produces silence rather than a stale position
with a fresh time. If your consumer needs an uninterrupted stream, use
`--gps-repeat`.

## GPX track playback

Accepts tracks (`<trkpt>`), routes (`<rtept>`) and loose waypoints (`<wpt>`),
with or without XML namespaces. Position is interpolated linearly between
points, so output is smooth at any `--rate`. COG is the bearing of the current
leg — with linear interpolation that is the exact direction of travel, not an
approximation. SOG is leg length divided by leg duration.

Real-world GPX files are messy, and three specific hazards are handled:

- **Nonsense `<ele>`.** Garmin exports write `1e25` to mean "no elevation".
  Values outside −1000…10000 m are rejected and the altitude field is left
  empty; the count of rejects is logged.
- **Relative timestamps.** Files from gpx.studio carry dates of `1970-01-01`.
  Stamps are used *only* as intervals between points; sentences get the current
  UTC time. Taking them literally would put `010170` in the `RMC` date field.
- **Duplicate points.** Zero-duration and zero-length legs are common. SOG and
  COG carry over from the last meaningful leg, otherwise speed would drop to
  zero mid-motion and the course would jump randomly.

`--gpx-speed` scales the playback rate but **not** the reported SOG. At `x30`
the vessel crosses the chart thirty times faster while still reporting the
recorded 2.7 kn. This is intentional: the alternative, 81 kn, is a fiction some
plotters reject outright. At `--gpx-speed 1` the question does not arise.

In GPX mode the compass is fed the current COG, so `HCHDM` and `HCHDG` work.
There is no source for attitude, so `YXXDR` is not emitted.

## Reliability

Intended to be left running for a whole passage, so failures are contained
rather than fatal:

- **Every long-running task is supervised.** A crash is logged with a full
  traceback and the task restarts with exponential backoff (1 s doubling to
  30 s). The backoff resets once a task has survived a minute, so a task that
  dies instantly cannot spin.
- **Unrecoverable problems stop immediately** with a readable message instead of
  retrying forever: a missing or malformed GPX file, absent WinRT libraries, a
  port already in use. The GPX file is validated *before* the port is bound, so
  a bad file never leaves a socket behind.
- **Sensors are re-acquired.** A compass or receiver missing at startup is
  retried every 15 seconds — USB hardware may appear later. A sensor that starts
  throwing errors is released after 10 consecutive failures and acquired again
  from scratch.
- **Repeated errors are rate-limited** so a failing sensor cannot flood the log.
- **Sentence-building errors cannot stop the broadcast**; they are logged and the
  loop continues.
- **Receiver status is reported.** `PositionStatus` transitions are logged, so
  `NO_DATA` or `DISABLED` is visible rather than silent.
- **A heartbeat line** every `--status-interval` seconds shows client count,
  sentences sent, fix age and position source.
- **Diagnostics go to stderr**, keeping stdout clean for `-v` sentence output so
  you can pipe one without losing the other.

Exit codes: `0` normal, `1` unrecoverable error, `2` bad command line, `130`
interrupted with `Ctrl+C`.

## Troubleshooting

**Plotter connects but shows nothing, or shows obviously wrong data.**
Check for a port conflict. Windows permits two processes to listen on the same
port if one binds `127.0.0.1` and the other `0.0.0.0` — and a client connecting
to `localhost` reaches the first one, not necessarily yours:

```powershell
Get-NetTCPConnection -LocalPort 10110 | Select-Object LocalAddress, State, OwningProcess
Get-Process -Id <OwningProcess>
```

**Ship icon points north and Dashboard shows `---`.**
Either heading sentences are not being sent — do not restrict `--sentences`, the
default includes `hdg` and `vtg` — or you are replaying GPX without
`--sim-as-real`. See [Connecting OpenCPN](#connecting-opencpn).

**No GNSS time in the Dashboard.**
Add the *GPS clock* instrument; *Local CPU clock* shows the computer's clock
instead. Confirm `GPRMC` or `GPZDA` is actually arriving with `-v`.

**Position is far from where you are.**
Look at the logged position source. `WI_FI` or `IP_ADDRESS` means Windows has no
satellite fix — go outdoors or near a window, and give the receiver a few
minutes for a cold start. Such fixes are correctly flagged invalid.

**`Geolocator: brak dostepu` / access denied.**
Location is off, or desktop apps are blocked. See
[Enabling location access](#enabling-location-access).

**`brak bibliotek WinRT`.**
Install the extras: `pip install -e ".[sensors]"`, or run `--simulate` / `--gpx`,
which need nothing.

**Empty SOG and COG fields in the sentences.**
Expected when the fix carries no motion measurement — Windows returns `NaN` for
speed and heading on WiFi and IP fixes. An empty field is correct NMEA; the
literal text `nan` would not be.

## Reference setup

The configuration this was developed and verified on, for comparison if yours
behaves differently:

| | |
|---|---|
| Laptop | Lenovo ThinkPad T480s (20L8S4PR0U) |
| OS | Windows 11 Pro 10.0.22631 (build 22631) |
| Python | 3.10.11 |
| GNSS | **Fibocom GNSS Sensor** — the GNSS half of a Fibocom L850-GL WWAN module (Intel XMM7360-P) |
| Compass / inclinometer | none — the `Sensor` device class contains only the GNSS sensor |
| Chart plotter | OpenCPN 5.12.4-0+37fd0cd |

The receiver is worth a note, because it is what makes this approach work: the
GNSS is integrated into the mobile-broadband card and is presented to Windows as
a **location sensor**, not as a serial port. There is no COM port to open, which
is precisely why going through the Sensors API is necessary rather than merely
convenient.

Measured values from that receiver, for a sense of what to expect: horizontal
accuracy 4.0 m, HDOP 0.5, PDOP 1.0, VDOP 0.75, with `GeometricDilutionOfPrecision`
and `TimeDilutionOfPrecision` both unsupported (`None`). Altitude is
geoid-referenced (`AltitudeReferenceSystem.GEOID`), so placing it in the
mean-sea-level field of `GPGGA` with an empty geoid-separation field is correct.

Since the test machine has no compass, the heading sentences are exercised
mainly through GPX playback and simulation. Reports from hardware with a real
compass are very welcome.

## Known limitations

- No `GPGSV` and no satellite count — Windows does not expose either
- No `GPGSA`, although PDOP and VDOP are available and could populate it
- The magnetic-course field of `GPVTG` is left empty; only true course is known
- No `GPGST`: Windows reports an accuracy radius rather than per-axis deviations,
  and `altitude_accuracy` is frequently `None`
- The TCP server has no authentication and no client limit. It is meant for a
  trusted local network; `--host 0.0.0.0` exposes it on every interface
- Sensor mode is Windows-only by nature. `--simulate` and `--gpx` are portable

## Disclaimer

**THIS SOFTWARE IS NOT A CERTIFIED NAVIGATION DEVICE AND MUST NOT BE USED AS A
PRIMARY MEANS OF NAVIGATION.**

`sensors2nmea` converts readings from consumer-grade hardware and operating
system APIs into NMEA 0183 sentences. The accuracy, availability, continuity and
integrity of that data are **not guaranteed**. In particular:

- positions may be missing, stale, or silently derived from WiFi or IP
  geolocation and wrong by tens of kilometres;
- the host operating system may suspend, throttle or deny sensor access at any
  moment, without warning;
- heading, speed and course are only as good as the consumer sensors behind
  them, and no integrity monitoring of any kind is performed;
- the GPX playback and simulation modes emit **entirely synthetic data by
  design**, and `--sim-as-real` makes that synthetic data deliberately
  indistinguishable from a real fix.

**The author accepts no responsibility and no liability for any grounding,
collision, allision, stranding, loss of or damage to any vessel, cargo or
property, nor for any injury or loss of life, arising from use of or reliance
upon this software or the data it produces, whether in whole or in part, however
caused.**

You, the mariner, remain fully and solely responsible for the safe navigation of
your vessel and for maintaining a proper lookout at all times. Navigate using
official charts and type-approved equipment, and always cross-check position
against independent sources.

This software is provided "AS IS", without warranty of any kind, express or
implied, including but not limited to the warranties of merchantability, fitness
for a particular purpose and non-infringement. See sections 7 and 8 of the
[Apache License 2.0](LICENSE) for the governing terms.

## License and attribution

Licensed under the **Apache License, Version 2.0** — see [LICENSE](LICENSE).

This is genuine open source: use it, modify it, redistribute it, build a
commercial product on it. One condition — **you must credit it.**

Section 4(d) of the licence requires that the [NOTICE](NOTICE) file be
reproduced in any derivative work you distribute. Concretely:

- Redistributing `sensors2nmea`, in source or binary form, modified or not,
  requires including a readable copy of `NOTICE` in **at least one** of: your
  source distribution, your documentation, or a display generated by your
  software such as an "About" or "Credits" screen.
- **This applies to commercial and closed-source products too.** Selling
  software that incorporates `sensors2nmea` is fine; removing the attribution is
  not.
- The name `sensors2nmea` and the author's name may not be used to endorse or
  promote your product without separate written permission.

If the Apache terms do not suit your use case, get in touch — other arrangements
are possible.

### Contributing

Issues and pull requests are welcome. Useful contributions in particular:

- reports from hardware that actually has a compass or inclinometer
- `GPGSA` and `GPGST` support built on the DOP values already available
- testing against plotters other than OpenCPN
