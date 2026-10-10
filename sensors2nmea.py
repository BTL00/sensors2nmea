#!/usr/bin/env python3
"""
sensors2nmea.py - Windows Sensors API -> NMEA 0183 over TCP.

Reads Geolocator, Compass and Inclinometer from Windows.Devices.* (WinRT) and
broadcasts NMEA 0183 sentences to every connected TCP client (OpenCPN,
Navionics, qtVlm and friends). It can also replay a recorded GPX track instead
of reading the sensors, which lets you test a plotter setup at your desk.

===============================================================================
  INSTALLATION
===============================================================================

Windows 10/11, Python 3.9+:

  pip install winrt-runtime winrt-Windows.Devices.Sensors \
              winrt-Windows.Devices.Geolocation winrt-Windows.Foundation

The older monolithic "winsdk" package also works - see import_winrt(), which
tries both. The --simulate and --gpx modes need neither.

Location access must be enabled in Settings > Privacy & security > Location,
otherwise Geolocator.request_access_async() is denied and no position arrives.

===============================================================================
  RUNNING
===============================================================================

  python sensors2nmea.py                       # port 10110, 0.0.0.0, 5 Hz
  python sensors2nmea.py --port 15555 -v       # other port; -v also echoes
                                               # sentences to stdout
  python sensors2nmea.py --port 10110,10111    # two ports, one stream: e.g.
                                               # OpenCPN and AvNav at once
  python sensors2nmea.py --sentences hdg       # compass heading only
  python sensors2nmea.py --simulate            # synthetic data, no sensors
  python sensors2nmea.py --gpx track.gpx --sim-as-real
  python sensors2nmea.py --gpx track.gpx --gpx-speed 10 --gpx-loop

Three mutually exclusive data sources, in order of precedence:

  --gpx FILE   replay a track from a GPX file
  --simulate   synthetic data (a circle off Szczecin)
  (default)    the real Windows sensors

===============================================================================
  SENTENCES
===============================================================================

Groups selected with --sentences (all of them by default):

  gps : GPGGA, GPRMC         position, altitude, SOG, COG, fix quality
  zda : GPZDA                UTC time, date, local zone offset
  vtg : GPVTG                course and speed over ground
  hdg : HCHDM, HCHDT, HCHDG  magnetic / true heading (Compass)
  att : YXXDR                pitch and roll (Inclinometer)

Practical note: many plotters read COG/SOG from VTG rather than from RMC. If the
OpenCPN Dashboard shows "---", first check that VTG is present in the stream.

===============================================================================
  TIME - WHY IT COMES FROM THE RECEIVER, NOT THE PC CLOCK
===============================================================================

Position sentences (GPGGA/GPRMC/GPVTG/GPZDA) are stamped with the fix time
(Geocoordinate.timestamp), not the time of transmission. This is not cosmetic:
Geolocator reports at roughly 1 Hz while --rate defaults to 5 Hz, so stamping
with the PC clock would send the same measurement five times under five
invented timestamps.

Consequence: each fix is transmitted EXACTLY ONCE, and when the receiver drops
a measurement the stream falls silent instead of repeating a stale position
under a fresh timestamp. The --gps-repeat flag restores continuous repetition
of the last fix at --rate for consumers that require an unbroken stream.

GPZDA also carries the local zone offset. The sign convention is documented
inconsistently across sources (NMEA 0183: negative for eastern longitudes;
Trimble: the other way round), so the intuitive reading is used here -
UTC + offset = local time, i.e. +02 for Poland on CEST. OpenCPN ignores those
fields anyway: it takes local time from the "Local offset from UTC" setting in
the Dashboard preferences.

===============================================================================
  FIX QUALITY - WHAT IS TRUE AND WHAT IS NOT
===============================================================================

Windows exposes neither the list of satellites nor their COUNT, so the
"satellites used" field in GPGGA is left EMPTY instead of carrying an invented
value. GPGSV therefore cannot be generated honestly at all. HDOP comes from the
real GeocoordinateSatelliteData.horizontal_dilution_of_precision; when the
hardware does not report it, that field stays empty too.

Fix quality follows Geocoordinate.position_source - see fix_quality():

  source                           GGA  RMC  FAA mode
  SATELLITE                          1    A    A
  WI_FI / CELLULAR / IP_ADDRESS      0    V    N
  DEFAULT / OBFUSCATED / UNKNOWN     0    V    N
  --simulate or --gpx                8    A    S
  ...with --sim-as-real              1    A    A

Only a satellite fix is a navigational fix. A position derived from WiFi, the
cellular network or an IP address is accurate to anywhere between hundreds of
metres and tens of kilometres, so it is flagged invalid - the coordinates still
travel in the sentence, but the consumer knows not to navigate on them. Every
change of source is logged.

On such a fix Windows returns NaN (not None) for speed and heading, because
there is no motion measurement. Every reading therefore passes through num(),
which maps NaN and infinities to None - otherwise the literal text "nan" ends
up in a numeric NMEA field and the sentence is malformed. A missing value
yields an empty field.

--sim-as-real is a deliberate lie on request: OpenCPN IGNORES data flagged as
simulator output (GGA 8 / mode S) and shows "---" for SOG and COG even though
the RMC status is "A" and the values are present. Without that flag, GPX
playback is invisible in it. Do not use it with real sensors - there, honest
source flagging matters and works correctly.

===============================================================================
  COMPASS HEADING
===============================================================================

Compass reports a magnetic heading, and sometimes a true one as well (Windows
computes it itself when it knows the declination for the current position).
Mapping onto sentences:

  HCHDM   magnetic heading, whenever the compass reports anything
  HCHDT   true heading - from the Windows reading, or derived from --variation
  HCHDG   magnetic heading plus a deviation/variation field computed from the
          difference between the two

  --offset DEGREES     correction added to the reading: mounting error, own
                       deviation. Applied to both headings before the fields
                       are computed.
  --variation DEGREES  magnetic variation (east positive). Used ONLY when
                       Windows does not supply a true heading - HCHDT is then
                       produced as magnetic heading plus variation.

Without --variation and without a true heading from Windows, only HCHDM is sent
(plus HCHDG with an empty variation field) - more honest than guessing the
declination.

Not every computer has a compass or an inclinometer; Compass.get_default() then
returns None, which is logged at startup, and the corresponding sentences
simply are not produced. Compass readings are subject to --stale independently
of the position.

===============================================================================
  GPX TRACK PLAYBACK
===============================================================================

Handles tracks (<trkpt>), routes (<rtept>) and loose waypoints (<wpt>),
regardless of XML namespace. Position is interpolated linearly between points,
so the output is smooth at any --rate; COG is the bearing of the current leg
(with linear interpolation that is the exact direction of travel, not an
approximation), and SOG is leg length divided by leg duration.

Three hazards of real-world files are handled explicitly:

  * <ele> is sometimes a "no data" sentinel - Garmin exports write 1e25 there.
    Values outside -1000..10000 m are rejected and the altitude field in GGA is
    left empty. The number of rejects is logged.

  * <time> is sometimes relative - files from gpx.studio carry dates of
    1970-01-01. Stamps are used ONLY as intervals between points; sentences get
    the current UTC time. Taking them as absolute would put 010170 in the RMC
    date field. When <time> is absent entirely, the pace comes from
    --gpx-knots.

  * duplicate points - legs of zero duration or zero length are common (in the
    track this was developed against, 206 of 594 legs had zero duration). On a
    zero-duration leg SOG and COG carry over from the last meaningful leg,
    otherwise the speed would drop to zero mid-motion and the course would jump
    at random.

--gpx-speed scales the playback pace but does NOT scale the reported SOG: at
x30 the vessel crosses the chart thirty times faster while still reporting the
recorded 2.7 kn. This is deliberate - the alternative, 81 kn, is a fiction that
some plotters reject as unrealistic. At --gpx-speed 1 the question does not
arise.

In GPX mode the compass is fed the current COG, so HCHDM/HCHDG work. There is
no source for attitude, so YXXDR is not emitted.

===============================================================================
  ARCHITECTURE
===============================================================================

Three concurrent asyncio tasks over a shared State object:

  data source     sensor_task / simulate_task / gpx_task - writes into State
  broadcast_loop  reads State, builds sentences, sends to clients at --rate
  serve_forever   accepts TCP connections - one listener per --port value,
                  all sharing a single client set, so every client receives
                  the same stream whichever port it connected to

State holds the most recent reading; every field is Optional, because "no
reading" is a normal condition rather than an error. The pos_seq counter is
incremented on every new fix and lets broadcast_loop tell whether the position
is new (see --gps-repeat above). Freshness is enforced by --stale, measured on
time.monotonic() so that a system clock change cannot affect operation.

The Geolocator callback arrives on a WinRT thread, so data is handed to the
asyncio loop through loop.call_soon_threadsafe() - without it this would be
unsynchronised concurrent writing into State.

gpx_sample() is a pure function extracted from the playback loop so that it can
be tested without a clock or sockets.

Every long-running task runs under supervise(), which logs a traceback and
restarts it with exponential backoff. Unrecoverable conditions raise Fatal and
terminate the program with a readable message instead of retrying forever.

===============================================================================
  KNOWN LIMITATIONS
===============================================================================

  * no GPGSV and no satellite count - Windows does not expose either
  * no GPGSA, although PDOP/VDOP are available in GeocoordinateSatelliteData
  * the magnetic course field in VTG is left empty - only true course is known
  * GPGST is not generated; Windows reports an accuracy radius rather than
    per-axis deviations, and altitude_accuracy is frequently None
  * the server has no client limit and no authentication - it is meant for a
    trusted local network; --host 0.0.0.0 exposes it on every interface
  * Windows lets two processes listen on the same port if one binds 127.0.0.1
    and the other 0.0.0.0 - a client on localhost then reaches the former.
    When things "sort of work but the data looks wrong", check for a port
    conflict with: Get-NetTCPConnection -LocalPort <port>
"""

import argparse
import asyncio
import math
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

KNOT_PER_MPS = 1.943844

__version__ = "1.0.1"


# --------------------------------------------------------------------------- #
# Diagnostics and resilience
# --------------------------------------------------------------------------- #
class Fatal(Exception):
    """An error there is no point in retrying: a bad file, a missing library, a
    port already in use. The supervisor does not restart a task that raises
    this - we exit with a readable message instead of looping with no prospect
    of success."""


def make_log(path=None):
    """Returns log(msg, level, exc). Writes to stderr, and to a file if asked.

    stderr rather than stdout, because stdout may be carrying the sentence
    stream under -v and be redirected into a pipe - diagnostics have to stay
    visible separately.
    """
    fh = None
    if path:
        try:
            fh = open(path, "a", encoding="utf-8")
        except OSError as e:
            print(f"WARNING: cannot write to {path}: {e}",
                  file=sys.stderr, flush=True)

    def log(msg, level="INFO", exc=False):
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        line = f"[{ts}] {level:<5} {msg}"
        if exc:
            line += "\n" + traceback.format_exc().rstrip()
        print(line, file=sys.stderr, flush=True)
        if fh:
            try:
                fh.write(line + "\n")
                fh.flush()
            except OSError:
                pass   # a full disk must not kill the broadcast

    return log


async def supervise(name, factory, log, max_delay=30.0, healthy_after=60.0):
    """Keeps a task running: logs a traceback on failure and restarts it.

    factory() builds a fresh coroutine on every attempt - the same object
    cannot be awaited twice. The delay doubles up to max_delay and resets to
    1 s once a task has survived healthy_after seconds; without that condition
    a task failing immediately after start would spin without pause. Fatal and
    CancelledError propagate - the first is unrecoverable, the second is the
    normal shutdown path.
    """
    delay = 1.0
    while True:
        started = time.monotonic()
        try:
            await factory()
            log(f"{name}: task returned on its own (unexpected)", "WARN")
        except asyncio.CancelledError:
            raise
        except Fatal:
            raise
        except Exception as e:
            alive = time.monotonic() - started
            if alive >= healthy_after:
                delay = 1.0
            log(f"{name}: failed after {alive:.0f}s ({type(e).__name__}: {e}); "
                f"restarting in {delay:.0f}s", "ERROR", exc=True)
        else:
            if time.monotonic() - started >= healthy_after:
                delay = 1.0
        await asyncio.sleep(delay)
        delay = min(delay * 2.0, max_delay)
        log(f"{name}: restarting", "WARN")


# --------------------------------------------------------------------------- #
# NMEA helpers
# --------------------------------------------------------------------------- #
def num(v) -> Optional[float]:
    """A finite number, or None.

    Windows returns NaN (not None) in Geocoordinate.speed and .heading when the
    fix carries no motion measurement - typically on a WiFi or IP-derived
    position. Without this filter the literal text "nan" lands in a numeric
    NMEA field and the sentence is malformed.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def nmea(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}\r\n"


def lat_field(lat: float):
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    deg = int(lat)
    minutes = (lat - deg) * 60.0
    return f"{deg:02d}{minutes:07.4f}", hemi


def lon_field(lon: float):
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    deg = int(lon)
    minutes = (lon - deg) * 60.0
    return f"{deg:03d}{minutes:07.4f}", hemi


class State:
    # Everything optional: "no reading" is a normal condition, not an error.
    def __init__(self):
        # position
        self.lat: Optional[float] = None
        self.lon: Optional[float] = None
        self.alt: Optional[float] = None
        self.sog: Optional[float] = None   # m/s
        self.cog: Optional[float] = None   # deg
        self.acc: Optional[float] = None   # m
        self.pos_time = 0.0                # monotonic
        self.pos_utc: Optional[datetime] = None  # fix time, UTC, from receiver
        self.pos_seq = 0     # fix counter, incremented on every new reading
        self.pos_source: Optional[str] = None  # SATELLITE / WI_FI / IP_ADDRESS
        self.hdop: Optional[float] = None  # real HDOP from SatelliteData
        self.gps_status: Optional[str] = None  # PositionStatus from Geolocator
        # compass
        self.hdg_mag: Optional[float] = None
        self.hdg_true: Optional[float] = None
        self.hdg_acc: Optional[int] = None
        self.hdg_time = 0.0
        # attitude
        self.pitch: Optional[float] = None
        self.roll: Optional[float] = None
        self.yaw: Optional[float] = None


def fmt_zone(total_minutes: int):
    """Offset in minutes -> (hours, minutes) in ZDA format.

    Per NMEA the minutes carry the same sign as the hours, so the sign goes on
    the hours field only and the minutes stay absolute (which matters for zones
    such as -03:30).
    """
    sign = "-" if total_minutes < 0 else ""
    m = abs(total_minutes)
    return f"{sign}{m // 60:02d}", f"{m % 60:02d}"


def zone_fields(utc: datetime):
    """Local zone offset in effect at the given UTC instant (DST aware)."""
    off = utc.astimezone().utcoffset()
    if off is None:
        return "", ""
    return fmt_zone(int(off.total_seconds()) // 60)


def fix_quality(s: State, args):
    """-> (GGA quality, RMC status, FAA mode indicator in RMC).

    Only a satellite fix is a navigational fix. A position from WiFi, the
    cellular network, an IP address, the Windows default location or one
    deliberately coarsened (OBFUSCATED) is accurate to between hundreds of
    metres and tens of kilometres and is unfit for navigation, so it is flagged
    invalid: GGA 0 + RMC V + mode N. The consumer decides whether to show it.
    """
    if args.simulate:
        # A deliberate lie on request: simulated data passes itself off as a GPS
        # fix, because some plotters ignore anything flagged as a simulator.
        if args.sim_as_real:
            return "1", "A", "A"
        return "8", "A", "S"   # 8 / S = simulation mode (NMEA)
    if s.pos_source == "SATELLITE":
        return "1", "A", "A"   # 1 / A = GPS fix, autonomous
    return "0", "V", "N"       # 0 / V / N = no valid fix


def build_sentences(s: State, args, emit_pos: bool = True) -> list:
    out = []
    mono = time.monotonic()

    # Fix time from the receiver; the PC clock only as a last-resort fallback.
    fix = s.pos_utc if s.pos_utc is not None else datetime.now(timezone.utc)
    t = fix.strftime("%H%M%S.") + f"{fix.microsecond // 10000:02d}"
    d = fix.strftime("%d%m%y")

    pos_ok = emit_pos and s.lat is not None and (mono - s.pos_time) < args.stale
    qual, status, mode = fix_quality(s, args)
    sog_kn = f"{s.sog * KNOT_PER_MPS:.1f}" if s.sog is not None else ""
    cog_t = f"{s.cog:.1f}" if s.cog is not None else ""

    if "gps" in args.sentences and pos_ok and s.lat is not None and s.lon is not None:
        la, lah = lat_field(s.lat)
        lo, loh = lon_field(s.lon)
        alt = f"{s.alt:.1f}" if s.alt is not None else ""
        # HDOP from the receiver only; Windows does not report the satellite
        # count, so that field stays empty rather than carrying a made-up value.
        hdop = f"{s.hdop:.1f}" if s.hdop is not None else ""
        out.append(nmea(f"GPGGA,{t},{la},{lah},{lo},{loh},{qual},,{hdop},{alt},M,,M,,"))
        out.append(nmea(f"GPRMC,{t},{status},{la},{lah},{lo},{loh},"
                        f"{sog_kn},{cog_t},{d},,,{mode}"))

    # VTG: course and speed over ground. Many plotters take COG/SOG from here
    # rather than from RMC. Magnetic course stays empty - only true is known.
    if "vtg" in args.sentences and pos_ok and s.cog is not None:
        kmh = f"{s.sog * 3.6:.1f}" if s.sog is not None else ""
        out.append(nmea(f"GPVTG,{cog_t},T,,M,{sog_kn},N,{kmh},K,{mode}"))

    if "zda" in args.sentences and pos_ok and s.pos_utc is not None:
        zh, zm = zone_fields(s.pos_utc)
        out.append(nmea(f"GPZDA,{t},{fix.strftime('%d')},{fix.strftime('%m')},"
                        f"{fix.strftime('%Y')},{zh},{zm}"))

    if "hdg" in args.sentences and s.hdg_mag is not None and (mono - s.hdg_time) < args.stale:
        hm = (s.hdg_mag + args.offset) % 360.0
        out.append(nmea(f"HCHDM,{hm:.1f},M"))
        ht = None
        if s.hdg_true is not None:
            ht = (s.hdg_true + args.offset) % 360.0
        elif args.variation is not None:
            ht = (hm + args.variation) % 360.0
        var = ","
        if ht is not None:
            out.append(nmea(f"HCHDT,{ht:.1f},T"))
            v = ((ht - hm + 180.0) % 360.0) - 180.0
            var = f"{abs(v):.1f},{'E' if v >= 0 else 'W'}"
        out.append(nmea(f"HCHDG,{hm:.1f},,,{var}"))

    if "att" in args.sentences and s.pitch is not None and s.roll is not None:
        out.append(nmea(f"YXXDR,A,{s.pitch:.1f},D,PTCH,A,{s.roll:.1f},D,ROLL"))

    return out


# --------------------------------------------------------------------------- #
# Sensor sources
# --------------------------------------------------------------------------- #
def import_winrt():
    """Returns the WinRT classes from winrt-* or from the older winsdk bundle.

    Having neither is Fatal: without the sensor libraries nothing can be read,
    and retrying the import will not change that. The --simulate and --gpx
    modes never come here, so they work without these packages.
    """
    try:
        from winrt.windows.devices.sensors import Compass, Inclinometer
        from winrt.windows.devices.geolocation import (
            Geolocator, PositionAccuracy, GeolocationAccessStatus, PositionStatus)
    except ImportError:
        try:
            from winsdk.windows.devices.sensors import Compass, Inclinometer
            from winsdk.windows.devices.geolocation import (
                Geolocator, PositionAccuracy, GeolocationAccessStatus, PositionStatus)
        except ImportError as e:
            raise Fatal(
                "WinRT libraries are missing: " + str(e) + "\n"
                "       install them with: pip install winrt-runtime "
                "winrt-Windows.Devices.Sensors winrt-Windows.Devices.Geolocation "
                "winrt-Windows.Foundation\n"
                "       or run without sensors: --simulate / --gpx FILE"
            ) from e
    return (Compass, Inclinometer, Geolocator, PositionAccuracy,
            GeolocationAccessStatus, PositionStatus)


SENSOR_RETRY_S = 15.0     # how often to try re-acquiring a missing sensor
MAX_READ_ERRORS = 10      # release a sensor after this many errors in a row


async def sensor_task(state: State, args, log):
    """Reads the Windows sensors. Tolerates missing and vanishing hardware.

    A sensor absent at startup is not a failure - it is retried every
    SENSOR_RETRY_S, because a USB GPS or a compass may appear later. A sensor
    that starts throwing errors is released after MAX_READ_ERRORS and acquired
    again from scratch; the logging itself is rate-limited so that a failure
    cannot flood the log.
    """
    (Compass, Inclinometer, Geolocator, PositionAccuracy,
     GeolocationAccessStatus, PositionStatus) = import_winrt()
    loop = asyncio.get_running_loop()

    want_hdg = "hdg" in args.sentences
    want_att = "att" in args.sentences
    # zda and vtg need a position too - without this, --sentences zda was dead
    want_gps = bool({"gps", "zda", "vtg"} & args.sentences)

    said = set()

    def once(key, msg, level="INFO"):
        """Logs once per state, so a missing sensor does not repeat every 15 s."""
        if key not in said:
            said.add(key)
            log(msg, level)

    def set_interval(dev, label):
        try:
            dev.report_interval = max(dev.minimum_report_interval,
                                      int(1000 / args.rate))
        except Exception as e:
            log(f"{label}: could not set report_interval: {e}", "WARN")

    def acquire(kind):
        cls, label = ((Compass, "Compass") if kind == "compass"
                      else (Inclinometer, "Inclinometer"))
        try:
            dev = cls.get_default()
        except Exception as e:
            once(f"{kind}_exc", f"{label}: acquisition failed: {e}", "ERROR")
            return None
        if dev is None:
            once(f"{kind}_none",
                 f"{label}: no sensor; retrying every {SENSOR_RETRY_S:.0f}s", "WARN")
            return None
        said.discard(f"{kind}_none")
        said.discard(f"{kind}_exc")
        set_interval(dev, label)
        log(f"{label}: OK")
        return dev

    def on_pos(sender, ev):
        try:
            c = ev.position.coordinate
            p = c.point.position
            sd = c.satellite_data
            data = dict(
                lat=p.latitude, lon=p.longitude, alt=p.altitude,
                sog=c.speed, cog=c.heading, acc=c.accuracy, ts=c.timestamp,
                src=str(getattr(c.position_source, "name", c.position_source)).upper(),
                hdop=None if sd is None else sd.horizontal_dilution_of_precision,
            )
        except Exception as e:
            data = e
        loop.call_soon_threadsafe(apply_pos, data)

    def apply_pos(data):
        if isinstance(data, Exception):
            log(f"Geolocator: failed to read position: {data}", "WARN")
            return
        ts = data["ts"]
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if data["src"] != state.pos_source:
            nav = ("navigational fix" if data["src"] == "SATELLITE"
                   else "NOT fit for navigation -> GGA 0 / RMC V")
            log(f"Geolocator: position source = {data['src']} ({nav})",
                "INFO" if data["src"] == "SATELLITE" else "WARN")
        # num() on everything: NaN from WinRT must not reach State
        state.lat, state.lon = num(data["lat"]), num(data["lon"])
        state.alt = num(data["alt"])
        state.sog, state.cog = num(data["sog"]), num(data["cog"])
        state.acc = num(data["acc"])
        state.hdop = num(data["hdop"])
        state.pos_source = data["src"]
        state.pos_utc = ts
        state.pos_time = time.monotonic()
        state.pos_seq += 1

    def on_status(sender, ev):
        try:
            s = str(getattr(ev.status, "name", ev.status)).upper()
        except Exception:
            return
        loop.call_soon_threadsafe(apply_status, s)

    def apply_status(s):
        if s == state.gps_status:
            return
        state.gps_status = s
        hint = {
            "READY": "",
            "INITIALIZING": "receiver is starting up",
            "NO_DATA": "no data from the receiver",
            "DISABLED": "location is switched off in Windows settings",
            "NOT_AVAILABLE": "no location hardware",
            "NOT_INITIALIZED": "not initialised yet",
        }.get(s, "")
        log(f"Geolocator: status = {s}" + (f" ({hint})" if hint else ""),
            "INFO" if s in ("READY", "INITIALIZING") else "WARN")

    async def acquire_geo():
        try:
            status = await Geolocator.request_access_async()
        except Exception as e:
            once("geo_access", f"Geolocator: request_access failed: {e}", "ERROR")
            return None
        if status != GeolocationAccessStatus.ALLOWED:
            once("geo_denied",
                 f"Geolocator: access denied ({status}). Settings > Privacy & "
                 "security > Location: enable location and desktop app access.",
                 "ERROR")
            return None
        try:
            g = Geolocator()
            g.desired_accuracy = PositionAccuracy.HIGH
            g.desired_accuracy_in_meters = 1
            g.report_interval = 1000
            g.movement_threshold = 0
            g.add_position_changed(on_pos)
            g.add_status_changed(on_status)
        except Exception as e:
            once("geo_init", f"Geolocator: initialisation failed: {e}", "ERROR")
            return None
        for k in ("geo_denied", "geo_access", "geo_init"):
            said.discard(k)
        log("Geolocator: OK (position_changed + status_changed)")
        return g

    compass = acquire("compass") if want_hdg else None
    incl = acquire("incl") if want_att else None
    geo = await acquire_geo() if want_gps else None
    if not (want_hdg or want_att or want_gps):
        raise Fatal("--sentences selects no sensor data at all")

    errors = {"compass": 0, "incl": 0}

    def read_error(kind, label, e):
        """Logs with rate limiting and says whether to release the sensor."""
        errors[kind] += 1
        n = errors[kind]
        if n == 1 or n == MAX_READ_ERRORS or n % 100 == 0:
            log(f"{label}: read error (x{n}): {e}", "WARN")
        if n >= MAX_READ_ERRORS:
            log(f"{label}: {n} errors in a row, releasing and re-acquiring",
                "ERROR")
            errors[kind] = 0
            return True
        return False

    period = 1.0 / args.rate
    next_retry = time.monotonic() + SENSOR_RETRY_S

    while True:
        if compass is not None:
            try:
                r = compass.get_current_reading()
                if r is not None:
                    state.hdg_mag = num(r.heading_magnetic_north)
                    state.hdg_true = num(r.heading_true_north)
                    try:
                        state.hdg_acc = int(r.heading_accuracy)
                    except Exception:
                        state.hdg_acc = None
                    # Nothing to stamp without a usable magnetic heading
                    if state.hdg_mag is not None:
                        state.hdg_time = time.monotonic()
                errors["compass"] = 0
            except Exception as e:
                if read_error("compass", "Compass", e):
                    compass = None

        if incl is not None:
            try:
                r = incl.get_current_reading()
                if r is not None:
                    state.pitch = num(r.pitch_degrees)
                    state.roll = num(r.roll_degrees)
                    state.yaw = num(r.yaw_degrees)
                errors["incl"] = 0
            except Exception as e:
                if read_error("incl", "Inclinometer", e):
                    incl = None

        now = time.monotonic()
        if now >= next_retry:
            next_retry = now + SENSOR_RETRY_S
            if want_hdg and compass is None:
                compass = acquire("compass")
            if want_att and incl is None:
                incl = acquire("incl")
            if want_gps and geo is None:
                geo = await acquire_geo()

        await asyncio.sleep(period)


# --------------------------------------------------------------------------- #
# GPX track playback
# --------------------------------------------------------------------------- #
EARTH_R = 6371000.0


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2.0 * EARTH_R * math.asin(math.sqrt(min(1.0, h)))


def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def parse_gpx(path, log):
    """GPX -> (kind of points, [(time|None, lat, lon, alt|None)]).

    Namespace agnostic. Prefers trkpt, then rtept, then wpt. An <ele> value is
    accepted only when physically plausible: Garmin exports write 1e25 there as
    a "no data" marker, which without the filter would yield an altitude of
    1e25 m in GPGGA.
    """
    import xml.etree.ElementTree as ET

    def tag(el):
        return el.tag.rsplit("}", 1)[-1]

    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        raise Fatal(f"GPX: no such file: {path}")
    except PermissionError:
        raise Fatal(f"GPX: permission denied: {path}")
    except ET.ParseError as e:
        raise Fatal(f"GPX: {path} is not well-formed XML: {e}")
    except OSError as e:
        raise Fatal(f"GPX: cannot read {path}: {e}")

    found = {"trkpt": [], "rtept": [], "wpt": []}
    dropped_ele = 0
    for el in root.iter():
        t = tag(el)
        if t not in found:
            continue
        try:
            lat, lon = float(el.get("lat")), float(el.get("lon"))
        except (TypeError, ValueError):
            continue
        alt = stamp = None
        for ch in el:
            ct, txt = tag(ch), (ch.text or "").strip()
            if ct == "ele" and txt:
                try:
                    v = float(txt)
                except ValueError:
                    v = None
                if v is None or not -1000.0 < v < 10000.0:
                    dropped_ele += 1
                else:
                    alt = v
            elif ct == "time" and txt:
                try:
                    stamp = datetime.fromisoformat(txt.replace("Z", "+00:00"))
                except ValueError:
                    stamp = None
        found[t].append((stamp, lat, lon, alt))

    for kind in ("trkpt", "rtept", "wpt"):
        if found[kind]:
            break
    else:
        raise Fatal(f"GPX: found no points (trkpt/rtept/wpt) in {path}")

    pts = found[kind]
    if len(pts) < 2:
        raise Fatal(f"GPX: playback needs at least 2 points, got {len(pts)}")
    log(f"GPX: {len(pts)} x {kind} from {path}")
    if dropped_ele:
        log(f"GPX: rejected {dropped_ele} implausible <ele> values "
            "-> altitude left empty in GGA")
    return kind, pts


def gpx_timeline(pts, knots, log):
    """[(time,lat,lon,alt)] -> ([(t_rel_s, lat, lon, alt)], pace description).

    The <time> stamps serve ONLY as INTERVALS. Files from gpx.studio carry dates
    of 1970-01-01, so as absolute times they are useless - sentences get the
    current UTC time instead. When <time> is absent entirely, the pace comes
    from --gpx-knots.
    """
    stamps = [p[0] for p in pts]
    if all(s is not None for s in stamps):
        base = stamps[0]
        rel = [(s - base).total_seconds() for s in stamps]
        for i in range(1, len(rel)):       # force non-decreasing
            if rel[i] < rel[i - 1]:
                rel[i] = rel[i - 1]
        years = sorted({s.year for s in stamps})
        mode = f"intervals from <time> (dates in file: {years} - ignored)"
    else:
        v = max(0.05, knots / KNOT_PER_MPS)
        rel = [0.0]
        for i in range(1, len(pts)):
            d = haversine(pts[i - 1][1], pts[i - 1][2], pts[i][1], pts[i][2])
            rel.append(rel[-1] + d / v)
        mode = f"no <time> in file -> constant {knots:g} kn"
    return [(rel[i], pts[i][1], pts[i][2], pts[i][3]) for i in range(len(pts))], mode


def gpx_sample(tl, el, i, last_cog, last_sog):
    """Track state at elapsed time el (seconds from the start of the track).

    A pure function, so it can be tested without a clock or sockets. The index
    and the last SOG/COG go in and come back out, because carrying them across
    legs of zero length or zero duration is part of the definition.
    Returns (i, lat, lon, alt, sog_mps, cog_deg).
    """
    # The index moves both ways - cheap, because time is monotonic.
    while i < len(tl) - 2 and tl[i + 1][0] <= el:
        i += 1
    while i > 0 and tl[i][0] > el:
        i -= 1

    ta, la1, lo1, a1 = tl[i]
    tb, la2, lo2, a2 = tl[i + 1]
    span = tb - ta
    # span == 0 is common: in the reference track 206 points share a timestamp
    # with their predecessor. Without this guard it would be division by zero.
    f = 0.0 if span <= 0.0 else min(1.0, max(0.0, (el - ta) / span))

    seg = haversine(la1, lo1, la2, lo2)
    if seg > 0.1:                      # zero-length legs must not change course
        last_cog = bearing(la1, lo1, la2, lo2)
    # 59 legs of the reference track have zero duration but non-zero length.
    # Without carrying the last speed over, SOG would drop to zero mid-motion.
    if span > 0.0:
        last_sog = seg / span

    alt = a1 if a2 is None else (a2 if a1 is None else a1 + (a2 - a1) * f)
    return (i, la1 + (la2 - la1) * f, lo1 + (lo2 - lo1) * f,
            alt, last_sog, last_cog)


def load_gpx(args, log):
    """Loads and validates the track. Called BEFORE the port is bound, so that
    a bad file fails immediately and readably instead of crashing after start."""
    _, pts = parse_gpx(args.gpx, log)
    tl, mode = gpx_timeline(pts, args.gpx_knots, log)
    dur = tl[-1][0]
    if dur <= 0.0:
        raise Fatal("GPX: the track has zero duration, nothing to replay")
    dist = sum(haversine(tl[i][1], tl[i][2], tl[i + 1][1], tl[i + 1][2])
               for i in range(len(tl) - 1))
    log(f"GPX: {mode}")
    log(f"GPX: length {dist:.0f} m ({dist / 1852.0:.2f} NM), "
        f"duration {dur:.0f} s, average {dist / dur * KNOT_PER_MPS:.1f} kn")
    log(f"GPX: pace multiplier x{args.gpx_speed:g} -> playback takes "
        f"~{dur / args.gpx_speed:.0f} s" + (", looping" if args.gpx_loop else ""))
    return tl, dur


async def gpx_task(state: State, args, log, tl, dur):
    period = 1.0 / args.rate
    i = 0
    t0 = time.monotonic()
    last_cog = bearing(tl[0][1], tl[0][2], tl[1][1], tl[1][2])
    last_sog = 0.0
    ended = False

    while True:
        el = (time.monotonic() - t0) * args.gpx_speed
        if el >= dur:
            if args.gpx_loop:
                t0 = time.monotonic()
                i = 0
                el = 0.0
                log("GPX: end of track, starting over")
            else:
                if not ended:
                    ended = True
                    log(f"GPX: end of track; after {args.stale:g} s without a "
                        "refresh, sentences will stop being sent")
                await asyncio.sleep(period)
                continue

        i, lat, lon, alt, last_sog, last_cog = gpx_sample(tl, el, i, last_cog, last_sog)
        state.lat = lat
        state.lon = lon
        state.alt = alt
        state.sog = last_sog
        state.cog = last_cog
        state.acc = 3.0
        state.hdop = 0.9
        state.pos_source = "GPX"
        state.pos_utc = datetime.now(timezone.utc)
        state.pos_time = time.monotonic()
        state.pos_seq += 1
        if "hdg" in args.sentences:
            state.hdg_mag = last_cog    # compass aligned with course over ground
            state.hdg_true = None
            state.hdg_time = time.monotonic()
        await asyncio.sleep(period)


async def simulate_task(state: State, args, log):
    log("SIMULATE: synthetic data, no sensors")
    t0 = time.monotonic()
    while True:
        t = time.monotonic() - t0
        state.lat = 53.4285 + 0.0005 * math.sin(t / 30.0)
        state.lon = 14.5528 + 0.0005 * math.cos(t / 30.0)
        state.alt = 5.0
        state.sog = 2.5
        state.cog = (t * 3.0) % 360.0
        state.acc = 3.0
        state.hdop = 0.9
        state.pos_source = "SIMULATED"
        state.pos_utc = datetime.now(timezone.utc)
        state.pos_time = time.monotonic()
        state.pos_seq += 1
        state.hdg_mag = (t * 3.0) % 360.0
        state.hdg_true = None
        state.hdg_time = time.monotonic()
        state.pitch = 2.0 * math.sin(t)
        state.roll = 5.0 * math.sin(t / 2.0)
        await asyncio.sleep(1.0 / args.rate)


# --------------------------------------------------------------------------- #
# TCP server
# --------------------------------------------------------------------------- #
class Server:
    def __init__(self, args, log):
        self.args = args
        self.log = log
        self.clients = set()
        self.sent = 0          # sentence counter, for the heartbeat
        self.build_errors = 0

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        # With several --port values the local port says which client is which
        local = writer.get_extra_info("sockname")
        if local and len(self.args.ports) > 1:
            peer = f"{peer} on port {local[1]}"
        self.clients.add(writer)
        self.log(f"TCP: connected {peer} (clients: {len(self.clients)})")
        try:
            # NMEA clients usually only read; wait for the disconnect.
            while await reader.read(1024):
                pass
        except Exception as e:
            self.log(f"TCP: {peer} broke off: {type(e).__name__}: {e}", "WARN")
        finally:
            self.clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
            self.log(f"TCP: disconnected {peer} (clients: {len(self.clients)})")

    async def broadcast_loop(self, state: State):
        period = 1.0 / self.args.rate
        last_seq = -1
        while True:
            # Without --gps-repeat, position sentences go out only on a new fix,
            # so the same measurement is not broadcast under invented times.
            emit_pos = self.args.gps_repeat or state.pos_seq != last_seq
            last_seq = state.pos_seq
            # A sentence-building error must not stop the broadcast: log the
            # first and every hundredth, and keep the loop going.
            try:
                sentences = build_sentences(state, self.args, emit_pos)
            except Exception:
                self.build_errors += 1
                if self.build_errors == 1 or self.build_errors % 100 == 0:
                    self.log(f"build_sentences: error (x{self.build_errors})",
                             "ERROR", exc=True)
                sentences = []
            if sentences:
                payload = "".join(sentences).encode("ascii", "replace")
                self.sent += len(sentences)
                if self.args.verbose:
                    try:
                        sys.stdout.write("".join(sentences))
                        sys.stdout.flush()
                    except OSError:
                        pass   # a closed pipe must not kill the broadcast
                for w in list(self.clients):
                    try:
                        w.write(payload)
                        await w.drain()
                    except Exception:
                        self.clients.discard(w)
                        try:
                            w.close()
                        except Exception:
                            pass
            await asyncio.sleep(period)

    async def status_loop(self, state: State):
        """Periodic status line - so that after hours it is clear it is alive."""
        every = self.args.status_interval
        last = 0
        while True:
            await asyncio.sleep(every)
            age = time.monotonic() - state.pos_time if state.pos_time else None
            pos = ("none" if age is None
                   else f"{age:.0f}s ago" if age < 86400 else "none")
            self.log(
                f"status: clients={len(self.clients)} sentences={self.sent} "
                f"(+{self.sent - last}) position={pos} "
                f"source={state.pos_source or '-'} gps={state.gps_status or '-'}"
            )
            last = self.sent


# --------------------------------------------------------------------------- #
def parse_args():
    p = argparse.ArgumentParser(
        description="Windows Sensors API -> NMEA 0183 over TCP")
    p.add_argument("--host", default="0.0.0.0",
                   help="listen address (default 0.0.0.0, every interface)")
    p.add_argument("--port", default="10110", metavar="N[,N...]",
                   help="TCP port, or a comma-separated list of ports to "
                        "listen on at once - the same sentences go to every "
                        "client on every port (default 10110, the IANA port "
                        "for NMEA 0183)")
    p.add_argument("--rate", type=float, default=5.0,
                   help="broadcast frequency in Hz (default 5)")
    p.add_argument("--sentences", default="gps,zda,vtg,hdg,att",
                   help="comma-separated list: gps,zda,vtg,hdg,att (default all)")
    p.add_argument("--sim-as-real", action="store_true",
                   help="in simulate/GPX mode, present the data as an ordinary "
                        "GPS fix (GGA 1, mode A) instead of flagging it as "
                        "simulation (GGA 8, mode S). Needed when the plotter "
                        "ignores data flagged as simulator output")
    p.add_argument("--gps-repeat", action="store_true",
                   help="repeat the last fix at --rate; by default each fix is "
                        "sent once, carrying its own time from the receiver")
    p.add_argument("--offset", type=float, default=0.0,
                   help="heading correction in degrees added to the compass "
                        "reading (mounting error, deviation)")
    p.add_argument("--variation", type=float, default=None,
                   help="magnetic variation in degrees (east positive), used "
                        "only when Windows does not supply a true heading")
    p.add_argument("--stale", type=float, default=5.0,
                   help="stop sending data after this many seconds without a "
                        "fresh reading (default 5)")
    p.add_argument("--simulate", action="store_true",
                   help="synthetic test data instead of the sensors")
    p.add_argument("--gpx", metavar="FILE",
                   help="replay a track or route from a GPX file instead of "
                        "reading the sensors; implies simulation mode")
    p.add_argument("--gpx-speed", type=float, default=1.0, metavar="X",
                   help="playback pace multiplier: 1 = real time, 10 = ten "
                        "times faster")
    p.add_argument("--gpx-knots", type=float, default=5.0, metavar="KN",
                   help="speed used only when the GPX has no <time> stamps "
                        "(default 5)")
    p.add_argument("--gpx-loop", action="store_true",
                   help="start over from the beginning at the end of the track")
    p.add_argument("--status-interval", type=float, default=60.0, metavar="S",
                   help="seconds between status log lines; 0 disables "
                        "(default 60)")
    p.add_argument("--log-file", metavar="FILE",
                   help="append diagnostics to a file as well as stderr")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="also print sentences to stdout")
    p.add_argument("--version", action="version",
                   version=f"sensors2nmea {__version__}")
    a = p.parse_args()
    a.sentences = {x.strip().lower() for x in a.sentences.split(",") if x.strip()}
    known = {"gps", "zda", "vtg", "hdg", "att"}
    bad = a.sentences - known
    if bad:
        p.error(f"--sentences: unknown groups {sorted(bad)}; available: "
                f"{','.join(sorted(known))}")
    if not a.sentences:
        p.error("--sentences must not be empty")
    if a.gpx_speed <= 0:
        p.error("--gpx-speed must be > 0")
    if a.rate <= 0:
        p.error("--rate must be > 0")
    if a.stale <= 0:
        p.error("--stale must be > 0")
    # --port accepts "10110" or "10110,10111,...". Duplicates are dropped
    # (binding the same port twice would fail), order is preserved.
    ports = []
    for item in a.port.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            n = int(item)
        except ValueError:
            p.error(f"--port: {item!r} is not a number")
        if not 1 <= n <= 65535:
            p.error(f"--port: {n} is outside the range 1-65535")
        if n not in ports:
            ports.append(n)
    if not ports:
        p.error("--port must name at least one port")
    a.ports = ports
    # A replayed track is simulated data, so it has to be flagged as such in
    # NMEA (GGA 8 / RMC mode S) rather than pose as a satellite fix.
    if a.gpx:
        a.simulate = True
    return a


async def main():
    args = parse_args()

    log = make_log(args.log_file)
    log(f"sensors2nmea {__version__} starting (Python {sys.version.split()[0]})")

    state = State()

    # The data source is chosen once; the GPX file is validated BEFORE the port
    # is bound, so a bad file never leaves a listening socket behind.
    if args.gpx:
        tl, dur = load_gpx(args, log)

        def make_source():
            return gpx_task(state, args, log, tl, dur)
    elif args.simulate:
        def make_source():
            return simulate_task(state, args, log)
    else:
        def make_source():
            return sensor_task(state, args, log)

    # One listener per port, all feeding the same Server: a client on any port
    # lands in the same client set and receives the same sentences. This is
    # how two plotters that each insist on their own port (OpenCPN on 10110,
    # AvNav on 10111, say) can read one stream at the same time.
    server = Server(args, log)
    listeners = []
    for port in args.ports:
        try:
            listeners.append(
                await asyncio.start_server(server.handle, args.host, port))
        except OSError as e:
            for srv in listeners:   # do not leave earlier ports bound
                srv.close()
            # WSAEADDRINUSE lands in errno (asyncio wraps the error), not
            # winerror; 48/98 are the BSD/Linux equivalents.
            hint = ""
            in_use = {10048, 48, 98}
            if e.errno in in_use or getattr(e, "winerror", None) in in_use:
                hint = (f"\n       port {port} is already in use. Find out by "
                        f"what with:\n         Get-NetTCPConnection -LocalPort "
                        f"{port}\n       then pick another one with --port")
            raise Fatal(f"cannot listen on {args.host}:{port}: {e}{hint}")

    ports = ",".join(str(x) for x in args.ports)
    log(f"TCP: listening on {args.host}:{ports}, {args.rate:g} Hz, "
        f"sentences: {','.join(sorted(args.sentences))}")
    if args.host == "0.0.0.0":
        log("TCP: 0.0.0.0 exposes the server on every interface; on an "
            "untrusted network use --host 127.0.0.1", "WARN")

    tasks = [srv.serve_forever() for srv in listeners]
    tasks += [
        supervise("data source", make_source, log),
        supervise("broadcast", lambda: server.broadcast_loop(state), log),
    ]
    if args.status_interval > 0:
        tasks.append(supervise("status", lambda: server.status_loop(state), log))

    try:
        await asyncio.gather(*tasks)
    finally:
        for srv in listeners:
            srv.close()
        for srv in listeners:
            await srv.wait_closed()


def cli():
    """Console entry point (see pyproject.toml). Returns the exit code."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\ninterrupted by user", file=sys.stderr)
        return 130
    except Fatal as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception:
        print("UNHANDLED ERROR:\n" + traceback.format_exc(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
