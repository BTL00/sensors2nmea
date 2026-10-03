#!/usr/bin/env python3
"""
sensors2nmea.py - Windows Sensors API -> NMEA 0183 przez TCP.

Czyta Geolocator, Compass i Inclinometer z Windows.Devices.* (WinRT) i rozglasza
zdania NMEA 0183 do wszystkich podlaczonych klientow TCP (OpenCPN, Navionics,
qtVlm itp.). Potrafi tez odtworzyc zapisana trase z pliku GPX zamiast czytac
czujniki - do testowania plotera bez wychodzenia w morze.

===============================================================================
  INSTALACJA
===============================================================================

Windows 10/11, Python 3.9+:

  pip install winrt-runtime winrt-Windows.Devices.Sensors \
              winrt-Windows.Devices.Geolocation winrt-Windows.Foundation

Alternatywnie starszy monolit: pip install winsdk (kod obsluguje oba - patrz
import_winrt()). Tryby --simulate i --gpx nie wymagaja zadnego z nich.

Dostep do lokalizacji musi byc wlaczony w Ustawienia > Prywatnosc > Lokalizacja,
inaczej Geolocator.request_access_async() zwroci odmowe i pozycji nie bedzie.

===============================================================================
  URUCHOMIENIE
===============================================================================

  python sensors2nmea.py                       # port 10110, 0.0.0.0, 5 Hz
  python sensors2nmea.py --port 15555 -v       # inny port, -v drukuje zdania
                                               # takze na stdout (diagnostyka)
  python sensors2nmea.py --sentences hdg       # tylko kurs z kompasu
  python sensors2nmea.py --simulate            # dane syntetyczne, bez czujnikow
  python sensors2nmea.py --gpx trasa.gpx --sim-as-real
  python sensors2nmea.py --gpx trasa.gpx --gpx-speed 10 --gpx-loop

Trzy zrodla danych, wzajemnie wykluczajace sie (pierwszenstwo w tej kolejnosci):

  --gpx PLIK   odtwarzanie trasy z pliku GPX
  --simulate   dane syntetyczne (okrag wokol Szczecina)
  (domyslnie)  prawdziwe czujniki Windows

===============================================================================
  ZDANIA
===============================================================================

Grupy wybierane przez --sentences (domyslnie wszystkie):

  gps : GPGGA, GPRMC         pozycja, wysokosc, SOG, COG, jakosc fixa
  zda : GPZDA                czas UTC, data, offset strefy lokalnej
  vtg : GPVTG                kurs i predkosc nad dnem
  hdg : HCHDM, HCHDT, HCHDG  kurs magnetyczny / rzeczywisty (Compass)
  att : YXXDR                przechyl i trym (Inclinometer)

Uwaga praktyczna: czesc ploterow czyta COG/SOG z VTG, a nie z RMC. Jesli
Dashboard w OpenCPN pokazuje "---", najpierw sprawdz, czy VTG jest w strumieniu.

===============================================================================
  CZAS - DLACZEGO Z ODBIORNIKA, A NIE Z ZEGARA PC
===============================================================================

Zdania pozycyjne (GPGGA/GPRMC/GPVTG/GPZDA) sa znakowane czasem fixa
(Geocoordinate.timestamp), nie czasem nadania. To nie kosmetyka: Geolocator
raportuje ok. 1 Hz, a --rate domyslnie 5 Hz, wiec przy stemplowaniu zegarem PC
ten sam pomiar szedlby pieciokrotnie z pieciu roznymi, zmyslonymi znacznikami.

Konsekwencja: kazdy fix jest wysylany DOKLADNIE RAZ, a gdy odbiornik zgubi
pomiar, w strumieniu pojawia sie cisza zamiast starej pozycji pod nowym czasem.
Flaga --gps-repeat przywraca powtarzanie ostatniego fixa z czestotliwoscia
--rate dla odbiorcow, ktorzy wymagaja nieprzerwanego strumienia.

GPZDA niesie tez offset strefy lokalnej. Konwencja znaku jest w standardzie
udokumentowana sprzecznie (NMEA 0183: ujemny dla dlugosci wschodnich; Trimble:
odwrotnie), tu uzyta jest interpretacja intuicyjna - UTC + offset = czas
lokalny, czyli +02 dla Polski w CEST. OpenCPN i tak tych pol nie czyta: czas
lokalny bierze z ustawienia "Local offset from UTC" w preferencjach Dashboard.

===============================================================================
  JAKOSC FIXA - CO JEST PRAWDA, A CO NIE
===============================================================================

Windows nie udostepnia listy satelitow ani ich LICZBY, wiec pole "satellites
used" w GPGGA zostaje PUSTE zamiast zmyslonej wartosci. GPGSV jest z tego
powodu niemozliwe do uczciwego wygenerowania. HDOP pochodzi z prawdziwego
GeocoordinateSatelliteData.horizontal_dilution_of_precision; gdy sprzet go nie
podaje, pole tez zostaje puste.

Jakosc fixa wynika z Geocoordinate.position_source - patrz fix_quality():

  zrodlo                           GGA  RMC  tryb FAA
  SATELLITE                          1    A    A
  WI_FI / CELLULAR / IP_ADDRESS      0    V    N
  DEFAULT / OBFUSCATED / UNKNOWN     0    V    N
  --simulate lub --gpx               8    A    S
  ...z flaga --sim-as-real           1    A    A

Tylko fix satelitarny jest fixem nawigacyjnym. Pozycja z WiFi, sieci komorkowej
lub adresu IP ma dokladnosc od setek metrow do dziesiatek kilometrow, wiec jest
oznaczana jako nieprawidlowa - wspolrzedne nadal ida w zdaniu, ale odbiorca wie,
ze nie wolno na nich nawigowac. Zmiana zrodla trafia do logu.

Przy takim fixie Windows zwraca NaN (a nie None) w speed i heading, bo nie ma
pomiaru ruchu. Wszystkie odczyty przechodza wiec przez num(), ktore zamienia
NaN i nieskonczonosci na None - inaczej do pola liczbowego NMEA trafia tekst
"nan" i zdanie jest formalnie niepoprawne. Brakujaca wartosc daje puste pole.

--sim-as-real to swiadome klamstwo na zyczenie: OpenCPN IGNORUJE dane oznaczone
jako symulator (GGA 8 / tryb S) i pokazuje "---" w SOG i COG, mimo ze status RMC
jest "A" i wartosci sa obecne. Bez tej flagi odtwarzanie GPX nie bedzie w nim
widoczne. Nie uzywaj jej z prawdziwymi czujnikami - tam uczciwe oznaczanie
zrodla ma znaczenie i dziala poprawnie.

===============================================================================
  KURS Z KOMPASU
===============================================================================

Compass podaje kurs magnetyczny, a czasem takze rzeczywisty (Windows liczy go
sam, jesli zna deklinacje dla biezacej pozycji). Odwzorowanie na zdania:

  HCHDM   kurs magnetyczny, zawsze gdy kompas cokolwiek podaje
  HCHDT   kurs rzeczywisty - z odczytu Windows, albo wyliczony z --variation
  HCHDG   kurs magnetyczny + pole dewiacji/deklinacji policzone z roznicy

  --offset STOPNIE     korekta dodawana do odczytu: blad montazu, dewiacja
                       wlasna. Stosowana do obu kursow, przed obliczeniem pol.
  --variation STOPNIE  deklinacja magnetyczna (E dodatnia). Uzywana TYLKO gdy
                       Windows nie podaje kursu rzeczywistego - wtedy HCHDT
                       powstaje jako kurs magnetyczny + deklinacja.

Bez --variation i bez kursu rzeczywistego z Windows wysylane jest samo HCHDM
(oraz HCHDG z pustym polem deklinacji) - uczciwiej niz zgadywac deklinacje.

Nie kazdy komputer ma kompas ani inklinometr; Compass.get_default() zwraca
wtedy None, co jest logowane przy starcie, a odpowiednie zdania po prostu nie
powstaja. Odczyty kompasu podlegaja --stale niezaleznie od pozycji.

===============================================================================
  ODTWARZANIE GPX
===============================================================================

Obsluguje track (<trkpt>), route (<rtept>) i luzne <wpt>, niezaleznie od
namespace'u. Pozycja jest interpolowana liniowo miedzy punktami, wiec wyjscie
jest gladkie przy dowolnym --rate; COG to azymut odcinka (przy interpolacji
liniowej to dokladny kierunek ruchu, nie przyblizenie), a SOG to dlugosc odcinka
podzielona przez jego czas.

Trzy pulapki realnych plikow, ktore kod obsluguje wprost:

  * <ele> bywa sentinelem "brak danych" - eksporty Garmina wpisuja tam 1e25.
    Wartosci poza zakresem -1000..10000 m sa odrzucane, a wysokosc w GGA
    zostaje pusta. Liczba odrzuconych trafia do logu.

  * <time> bywa wzgledny - pliki z gpx.studio maja daty 1970-01-01. Znaczniki
    sluza WYLACZNIE jako odstepy miedzy punktami; zdania dostaja biezacy czas
    UTC. Wstawienie ich jako czasu absolutnego dalo by w RMC date 010170.
    Gdy <time> nie ma wcale, tempo bierze sie z --gpx-knots.

  * punkty zdublowane - odcinki o zerowym czasie lub zerowej dlugosci sa
    powszechne (w testowej trasie 206 z 594 mialo zerowy czas). Przy zerowym
    czasie SOG i COG sa przenoszone z ostatniego sensownego odcinka, inaczej
    predkosc spadalaby do zera mimo ruchu, a kurs skakalby losowo.

--gpx-speed skaluje tempo odtwarzania, ale NIE skaluje raportowanego SOG: przy
x30 statek przemierza mape trzydziestokrotnie szybciej, wciaz podajac prawdziwe
2.7 kn z zapisu. Jest to celowe - alternatywa (81 kn) bylaby fikcja, ktora czesc
ploterow odrzuca jako nierealna. Przy --gpx-speed 1 problem nie istnieje.

W trybie GPX kompas dostaje kurs rowny COG, wiec HCHDM/HCHDG dzialaja.
Przechylow nie ma skad wziac, wiec YXXDR nie jest wysylane.

===============================================================================
  ARCHITEKTURA
===============================================================================

Trzy wspolbiezne zadania asyncio nad wspolnym obiektem State:

  zrodlo danych   sensor_task / simulate_task / gpx_task - zapisuje do State
  broadcast_loop  czyta State, buduje zdania, rozsyla do klientow z --rate
  serve_forever   przyjmuje polaczenia TCP

State trzyma ostatni odczyt; wszystkie pola sa Optional, bo "brak odczytu" to
normalny stan, nie blad. Licznik pos_seq rosnie przy kazdym nowym fixie i sluzy
broadcast_loop do rozpoznania, czy pozycja jest nowa (patrz --gps-repeat wyzej).
Swiezosc pilnuje --stale, liczony na time.monotonic(), zeby zmiana zegara
systemowego nie wplynela na dzialanie.

Callback Geolocatora przychodzi z watku WinRT, wiec dane sa przekazywane do
petli asyncio przez loop.call_soon_threadsafe() - bez tego byloby to
niezabezpieczone wspolbiezne pisanie do State.

gpx_sample() jest czysta funkcja wydzielona z petli odtwarzania, zeby dala sie
testowac bez zegara i gniazd.

===============================================================================
  ZNANE OGRANICZENIA
===============================================================================

  * brak GPGSV i liczby satelitow - Windows tego nie udostepnia
  * brak GPGSA, choc PDOP/VDOP sa dostepne w GeocoordinateSatelliteData
  * kurs magnetyczny w VTG zostaje pusty - znany jest tylko rzeczywisty
  * GPGST nie jest generowane; Windows podaje promien dokladnosci, nie
    odchylenia na osie, a altitude_accuracy czesto jest None
  * serwer nie ma limitu liczby klientow ani uwierzytelniania - przeznaczony do
    sieci lokalnej; --host 0.0.0.0 wystawia go na wszystkie interfejsy
  * Windows pozwala dwoma procesom nasluchiwac na tym samym porcie, jesli jeden
    wiaze 127.0.0.1, a drugi 0.0.0.0 - wtedy klient z localhost trafia do tego
    pierwszego. Przy "niby dziala, ale dane sa dziwne" sprawdz konflikt portu
    przez: Get-NetTCPConnection -LocalPort <port>
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

__version__ = "1.0.0"


# --------------------------------------------------------------------------- #
# Diagnostyka i odpornosc
# --------------------------------------------------------------------------- #
class Fatal(Exception):
    """Blad, ktorego ponawianie nie ma sensu: zly plik, brak biblioteki, zajety
    port. Nadzorca nie restartuje zadania, ktore to zglosi - konczymy z
    czytelnym komunikatem zamiast petlic bez szans na sukces."""


def make_log(path=None):
    """Zwraca log(msg, level, exc). Pisze na stderr, opcjonalnie tez do pliku.

    stderr, nie stdout, bo stdout moze byc zajety przez -v (strumien zdan) i
    przekierowany do potoku - diagnostyka musi byc widoczna osobno.
    """
    fh = None
    if path:
        try:
            fh = open(path, "a", encoding="utf-8")
        except OSError as e:
            print(f"UWAGA: nie moge pisac do {path}: {e}", file=sys.stderr, flush=True)

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
                pass   # dysk pelny nie moze zabic rozsylania zdan

    return log


async def supervise(name, factory, log, max_delay=30.0, healthy_after=60.0):
    """Trzyma zadanie w ruchu: po awarii loguje slad i restartuje z backoffem.

    factory() tworzy swiezy coroutine przy kazdej probie - nie da sie czekac
    dwa razy na ten sam obiekt. Opoznienie rosnie dwukrotnie do max_delay i
    wraca do 1 s, gdy zadanie przezylo healthy_after sekund; bez tego warunku
    zadanie padajace natychmiast po starcie krecilo by sie w kolko bez przerwy.
    Fatal i CancelledError przechodza w gore - pierwszy jest nieodwracalny,
    drugi to normalne zamykanie aplikacji.
    """
    delay = 1.0
    while True:
        started = time.monotonic()
        try:
            await factory()
            log(f"{name}: zadanie zakonczylo sie samo (nieoczekiwanie)", "WARN")
        except asyncio.CancelledError:
            raise
        except Fatal:
            raise
        except Exception as e:
            alive = time.monotonic() - started
            if alive >= healthy_after:
                delay = 1.0
            log(f"{name}: awaria po {alive:.0f}s ({type(e).__name__}: {e}); "
                f"restart za {delay:.0f}s", "ERROR", exc=True)
        else:
            if time.monotonic() - started >= healthy_after:
                delay = 1.0
        await asyncio.sleep(delay)
        delay = min(delay * 2.0, max_delay)
        log(f"{name}: restart", "WARN")


# --------------------------------------------------------------------------- #
# NMEA helpers
# --------------------------------------------------------------------------- #
def num(v) -> Optional[float]:
    """Liczba skonczona albo None.

    Windows zwraca NaN (nie None) w Geocoordinate.speed i .heading, gdy fix nie
    niesie pomiaru ruchu - typowo przy pozycji z WiFi lub adresu IP. Bez tego
    filtra do pola liczbowego NMEA trafia tekst "nan" i zdanie jest niepoprawne.
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
    # Wszystko opcjonalne: "brak odczytu" to normalny stan, a nie blad.
    def __init__(self):
        # position
        self.lat: Optional[float] = None
        self.lon: Optional[float] = None
        self.alt: Optional[float] = None
        self.sog: Optional[float] = None   # m/s
        self.cog: Optional[float] = None   # deg
        self.acc: Optional[float] = None   # m
        self.pos_time = 0.0                # monotonic
        self.pos_utc: Optional[datetime] = None  # czas fixa UTC (z odbiornika)
        self.pos_seq = 0     # licznik fixow, rosnie przy kazdym nowym odczycie
        self.pos_source: Optional[str] = None  # SATELLITE / WI_FI / IP_ADDRESS / ...
        self.hdop: Optional[float] = None  # realny HDOP z SatelliteData
        self.gps_status: Optional[str] = None  # PositionStatus z Geolocatora
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
    """Offset w minutach -> (godziny, minuty) w formacie ZDA.

    Wg NMEA minuty nosza ten sam znak co godziny, wiec znak trafia tylko na
    pole godzin, a minuty zostaja bezwzgledne (istotne dla stref typu -03:30).
    """
    sign = "-" if total_minutes < 0 else ""
    m = abs(total_minutes)
    return f"{sign}{m // 60:02d}", f"{m % 60:02d}"


def zone_fields(utc: datetime):
    """Offset strefy lokalnej obowiazujacy w podanej chwili UTC (uwzglednia DST)."""
    off = utc.astimezone().utcoffset()
    if off is None:
        return "", ""
    return fmt_zone(int(off.total_seconds()) // 60)


def fix_quality(s: State, args):
    """-> (jakosc GGA, status RMC, wskaznik trybu FAA w RMC).

    Tylko fix satelitarny jest fixem nawigacyjnym. Pozycja z WiFi, sieci
    komorkowej, adresu IP, lokalizacji domyslnej Windows albo celowo zgrubiona
    (OBFUSCATED) ma dokladnosc od kilkuset metrow do kilkudziesieciu kilometrow
    i nie nadaje sie do nawigacji, wiec jest oznaczana jako nieprawidlowa:
    GGA 0 + RMC V + tryb N. Odbiorca sam zdecyduje, czy ja pokazac.
    """
    if args.simulate:
        # Swiadome klamstwo na zyczenie: dane symulowane podaja sie za fix GPS,
        # bo czesc ploterow ignoruje wszystko oznaczone jako symulator.
        if args.sim_as_real:
            return "1", "A", "A"
        return "8", "A", "S"   # 8 / S = tryb symulacji (NMEA)
    if s.pos_source == "SATELLITE":
        return "1", "A", "A"   # 1 / A = fix GPS, autonomiczny
    return "0", "V", "N"       # 0 / V / N = brak prawidlowego fixa


def build_sentences(s: State, args, emit_pos: bool = True) -> list:
    out = []
    mono = time.monotonic()

    # Czas fixa z odbiornika; zegar PC tylko jako awaryjny fallback.
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
        # HDOP tylko z odbiornika; Windows nie podaje liczby satelitow, wiec to
        # pole zostaje puste zamiast zmyslonej wartosci.
        hdop = f"{s.hdop:.1f}" if s.hdop is not None else ""
        out.append(nmea(f"GPGGA,{t},{la},{lah},{lo},{loh},{qual},,{hdop},{alt},M,,M,,"))
        out.append(nmea(f"GPRMC,{t},{status},{la},{lah},{lo},{loh},"
                        f"{sog_kn},{cog_t},{d},,,{mode}"))

    # VTG: kurs i predkosc nad dnem. Wiele ploterow bierze COG/SOG wlasnie
    # stad, a nie z RMC. Kurs magnetyczny zostaje pusty - znamy tylko rzeczywisty.
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
    """Zwraca klasy WinRT z winrt-* albo ze starszego monolitu winsdk.

    Brak obu to Fatal: bez bibliotek czujnikow nie da sie nic odczytac, a
    ponawianie importu niczego nie zmieni. Tryby --simulate i --gpx tu nie
    zagladaja, wiec dzialaja bez tych pakietow.
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
                "brak bibliotek WinRT: " + str(e) + "\n"
                "       zainstaluj: pip install winrt-runtime "
                "winrt-Windows.Devices.Sensors winrt-Windows.Devices.Geolocation "
                "winrt-Windows.Foundation\n"
                "       albo uruchom bez czujnikow: --simulate / --gpx PLIK"
            ) from e
    return (Compass, Inclinometer, Geolocator, PositionAccuracy,
            GeolocationAccessStatus, PositionStatus)


SENSOR_RETRY_S = 15.0     # jak czesto probowac odzyskac brakujacy czujnik
MAX_READ_ERRORS = 10      # po tylu bledach z rzedu czujnik jest zwalniany


async def sensor_task(state: State, args, log):
    """Czyta czujniki Windows. Odporny na brak i utrate sprzetu w trakcie.

    Czujnik nieobecny przy starcie nie jest porazka - jest ponawiany co
    SENSOR_RETRY_S, bo USB GPS albo kompas moga pojawic sie pozniej. Czujnik,
    ktory zaczyna sypac bledami, jest zwalniany po MAX_READ_ERRORS i pozyskiwany
    od nowa; samo logowanie jest ograniczane, zeby awaria nie zalala dziennika.
    """
    (Compass, Inclinometer, Geolocator, PositionAccuracy,
     GeolocationAccessStatus, PositionStatus) = import_winrt()
    loop = asyncio.get_running_loop()

    want_hdg = "hdg" in args.sentences
    want_att = "att" in args.sentences
    # zda i vtg tez potrzebuja pozycji - bez tego --sentences zda nie dzialalo
    want_gps = bool({"gps", "zda", "vtg"} & args.sentences)

    said = set()

    def once(key, msg, level="INFO"):
        """Loguje raz na stan, zeby brak czujnika nie powtarzal sie co 15 s."""
        if key not in said:
            said.add(key)
            log(msg, level)

    def set_interval(dev, label):
        try:
            dev.report_interval = max(dev.minimum_report_interval,
                                      int(1000 / args.rate))
        except Exception as e:
            log(f"{label}: nie ustawiono report_interval: {e}", "WARN")

    def acquire(kind):
        cls, label = ((Compass, "Compass") if kind == "compass"
                      else (Inclinometer, "Inclinometer"))
        try:
            dev = cls.get_default()
        except Exception as e:
            once(f"{kind}_exc", f"{label}: blad pozyskania: {e}", "ERROR")
            return None
        if dev is None:
            once(f"{kind}_none",
                 f"{label}: brak czujnika; ponawiam co {SENSOR_RETRY_S:.0f}s", "WARN")
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
            log(f"Geolocator: blad odczytu pozycji: {data}", "WARN")
            return
        ts = data["ts"]
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if data["src"] != state.pos_source:
            nav = ("fix nawigacyjny" if data["src"] == "SATELLITE"
                   else "NIE nadaje sie do nawigacji -> GGA 0 / RMC V")
            log(f"Geolocator: zrodlo pozycji = {data['src']} ({nav})",
                "INFO" if data["src"] == "SATELLITE" else "WARN")
        # num() na wszystkim: NaN z WinRT nie moze dotrzec do State
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
            "INITIALIZING": "odbiornik sie uruchamia",
            "NO_DATA": "brak danych z odbiornika",
            "DISABLED": "lokalizacja wylaczona w ustawieniach Windows",
            "NOT_AVAILABLE": "brak sprzetu lokalizacji",
            "NOT_INITIALIZED": "jeszcze nie zainicjowany",
        }.get(s, "")
        log(f"Geolocator: status = {s}" + (f" ({hint})" if hint else ""),
            "INFO" if s in ("READY", "INITIALIZING") else "WARN")

    async def acquire_geo():
        try:
            status = await Geolocator.request_access_async()
        except Exception as e:
            once("geo_access", f"Geolocator: blad request_access: {e}", "ERROR")
            return None
        if status != GeolocationAccessStatus.ALLOWED:
            once("geo_denied",
                 f"Geolocator: brak dostepu ({status}). Ustawienia > Prywatnosc > "
                 "Lokalizacja: wlacz lokalizacje i dostep dla aplikacji klasycznych.",
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
            once("geo_init", f"Geolocator: blad inicjalizacji: {e}", "ERROR")
            return None
        for k in ("geo_denied", "geo_access", "geo_init"):
            said.discard(k)
        log("Geolocator: OK (position_changed + status_changed)")
        return g

    compass = acquire("compass") if want_hdg else None
    incl = acquire("incl") if want_att else None
    geo = await acquire_geo() if want_gps else None
    if not (want_hdg or want_att or want_gps):
        raise Fatal("--sentences nie wybiera zadnych danych z czujnikow")

    errors = {"compass": 0, "incl": 0}

    def read_error(kind, label, e):
        """Loguje z ograniczeniem i mowi, czy zwolnic czujnik."""
        errors[kind] += 1
        n = errors[kind]
        if n == 1 or n == MAX_READ_ERRORS or n % 100 == 0:
            log(f"{label}: blad odczytu (x{n}): {e}", "WARN")
        if n >= MAX_READ_ERRORS:
            log(f"{label}: {n} bledow z rzedu, zwalniam i pozyskam ponownie", "ERROR")
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
                    # Bez sensownego kursu magnetycznego nie ma czego stemplowac
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
# Odtwarzanie trasy z GPX
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
    """GPX -> (rodzaj punktow, [(czas|None, lat, lon, alt|None)]).

    Dziala bez wzgledu na namespace. Preferuje trkpt, potem rtept, potem wpt.
    <ele> przechodzi tylko gdy jest fizycznie sensowne: eksporty Garmina wpisuja
    tam 1e25 jako znacznik "brak danych", co bez filtra dalo by wysokosc
    1e25 m w GPGGA.
    """
    import xml.etree.ElementTree as ET

    def tag(el):
        return el.tag.rsplit("}", 1)[-1]

    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        raise Fatal(f"GPX: nie ma pliku {path}")
    except PermissionError:
        raise Fatal(f"GPX: brak uprawnien do {path}")
    except ET.ParseError as e:
        raise Fatal(f"GPX: plik {path} nie jest poprawnym XML: {e}")
    except OSError as e:
        raise Fatal(f"GPX: nie moge odczytac {path}: {e}")

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
        raise Fatal(f"GPX: nie znalazlem zadnych punktow (trkpt/rtept/wpt) w {path}")

    pts = found[kind]
    if len(pts) < 2:
        raise Fatal(f"GPX: potrzebne min. 2 punkty do odtwarzania, jest {len(pts)}")
    log(f"GPX: {len(pts)} x {kind} z {path}")
    if dropped_ele:
        log(f"GPX: odrzucono {dropped_ele} niesensownych <ele> -> wysokosc pusta w GGA")
    return kind, pts


def gpx_timeline(pts, knots, log):
    """[(czas,lat,lon,alt)] -> ([(t_rel_s, lat, lon, alt)], opis tempa).

    Znaczniki <time> sluza tylko jako ODSTEPY. Pliki z gpx.studio maja daty
    1970-01-01, wiec jako czas absolutny sa bezuzyteczne - zdania dostaja
    biezacy czas UTC. Gdy <time> nie ma wcale, tempo bierze sie z --gpx-knots.
    """
    stamps = [p[0] for p in pts]
    if all(s is not None for s in stamps):
        base = stamps[0]
        rel = [(s - base).total_seconds() for s in stamps]
        for i in range(1, len(rel)):       # wymus niemalejacosc
            if rel[i] < rel[i - 1]:
                rel[i] = rel[i - 1]
        years = sorted({s.year for s in stamps})
        mode = f"odstepy z <time> (daty w pliku: {years} - ignorowane)"
    else:
        v = max(0.05, knots / KNOT_PER_MPS)
        rel = [0.0]
        for i in range(1, len(pts)):
            d = haversine(pts[i - 1][1], pts[i - 1][2], pts[i][1], pts[i][2])
            rel.append(rel[-1] + d / v)
        mode = f"brak <time> w pliku -> stale {knots:g} kn"
    return [(rel[i], pts[i][1], pts[i][2], pts[i][3]) for i in range(len(pts))], mode


def gpx_sample(tl, el, i, last_cog, last_sog):
    """Stan trasy w chwili el (sekundy od startu trasy).

    Czysta funkcja, zeby dala sie przetestowac bez zegara i gniazd. Indeks i
    ostatnie SOG/COG wchodza i wychodza, bo przenoszenie ich przez odcinki
    zerowej dlugosci/zerowego czasu jest czescia definicji.
    Zwraca (i, lat, lon, alt, sog_mps, cog_deg).
    """
    # Indeks posuwa sie w obie strony - tanio, bo czas jest monotoniczny.
    while i < len(tl) - 2 and tl[i + 1][0] <= el:
        i += 1
    while i > 0 and tl[i][0] > el:
        i -= 1

    ta, la1, lo1, a1 = tl[i]
    tb, la2, lo2, a2 = tl[i + 1]
    span = tb - ta
    # span == 0 zdarza sie czesto: w tej trasie 206 punktow ma czas identyczny
    # z poprzednim. Bez tego byloby dzielenie przez zero.
    f = 0.0 if span <= 0.0 else min(1.0, max(0.0, (el - ta) / span))

    seg = haversine(la1, lo1, la2, lo2)
    if seg > 0.1:                      # zerowe segmenty nie zmieniaja kursu
        last_cog = bearing(la1, lo1, la2, lo2)
    # 59 odcinkow tej trasy ma zerowy czas przy niezerowej dlugosci. Bez
    # przeniesienia ostatniej predkosci SOG spadalby tam do zera mimo ruchu.
    if span > 0.0:
        last_sog = seg / span

    alt = a1 if a2 is None else (a2 if a1 is None else a1 + (a2 - a1) * f)
    return (i, la1 + (la2 - la1) * f, lo1 + (lo2 - lo1) * f,
            alt, last_sog, last_cog)


def load_gpx(args, log):
    """Wczytuje i waliduje trase. Wolane PRZED zajeciem portu, zeby zly plik
    konczyl sie natychmiastowym, czytelnym bledem, a nie awaria po starcie."""
    _, pts = parse_gpx(args.gpx, log)
    tl, mode = gpx_timeline(pts, args.gpx_knots, log)
    dur = tl[-1][0]
    if dur <= 0.0:
        raise Fatal("GPX: zerowy czas trwania trasy, nie ma czego odtwarzac")
    dist = sum(haversine(tl[i][1], tl[i][2], tl[i + 1][1], tl[i + 1][2])
               for i in range(len(tl) - 1))
    log(f"GPX: {mode}")
    log(f"GPX: dlugosc {dist:.0f} m ({dist / 1852.0:.2f} Mm), "
        f"czas {dur:.0f} s, srednio {dist / dur * KNOT_PER_MPS:.1f} kn")
    log(f"GPX: mnoznik tempa x{args.gpx_speed:g} -> odtwarzanie "
        f"~{dur / args.gpx_speed:.0f} s" + (", w petli" if args.gpx_loop else ""))
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
                log("GPX: koniec trasy, start od nowa")
            else:
                if not ended:
                    ended = True
                    log(f"GPX: koniec trasy; po {args.stale:g} s bez odswiezenia "
                        "zdania przestana byc wysylane")
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
            state.hdg_mag = last_cog    # kompas zgodny z kursem nad dnem
            state.hdg_true = None
            state.hdg_time = time.monotonic()
        await asyncio.sleep(period)


async def simulate_task(state: State, args, log):
    log("SIMULATE: dane syntetyczne, bez czujnikow")
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
        self.sent = 0          # licznik zdan, do heartbeatu
        self.build_errors = 0

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        self.clients.add(writer)
        self.log(f"TCP: polaczono {peer} (klientow: {len(self.clients)})")
        try:
            # Klienci NMEA zwykle tylko czytaja; czekamy na rozlaczenie.
            while await reader.read(1024):
                pass
        except Exception as e:
            self.log(f"TCP: {peer} przerwane: {type(e).__name__}: {e}", "WARN")
        finally:
            self.clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
            self.log(f"TCP: rozlaczono {peer} (klientow: {len(self.clients)})")

    async def broadcast_loop(self, state: State):
        period = 1.0 / self.args.rate
        last_seq = -1
        while True:
            # Bez --gps-repeat zdania pozycyjne ida tylko przy nowym fixie, zeby
            # nie rozsylac tego samego pomiaru pod kilkoma zmyslonymi czasami.
            emit_pos = self.args.gps_repeat or state.pos_seq != last_seq
            last_seq = state.pos_seq
            # Blad budowania zdania nie moze zatrzymac rozsylania: logujemy
            # pierwszy i co setny, a petla idzie dalej.
            try:
                sentences = build_sentences(state, self.args, emit_pos)
            except Exception:
                self.build_errors += 1
                if self.build_errors == 1 or self.build_errors % 100 == 0:
                    self.log(f"build_sentences: blad (x{self.build_errors})",
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
                        pass   # zamkniety potok nie moze zabic rozsylania
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
        """Okresowy wiersz stanu - zeby po godzinach bylo widac, ze zyje."""
        every = self.args.status_interval
        last = 0
        while True:
            await asyncio.sleep(every)
            age = time.monotonic() - state.pos_time if state.pos_time else None
            pos = ("brak" if age is None
                   else f"{age:.0f}s temu" if age < 86400 else "brak")
            self.log(
                f"status: klientow={len(self.clients)} zdan={self.sent} "
                f"(+{self.sent - last}) pozycja={pos} "
                f"zrodlo={state.pos_source or '-'} gps={state.gps_status or '-'}"
            )
            last = self.sent


# --------------------------------------------------------------------------- #
def parse_args():
    p = argparse.ArgumentParser(description="Windows Sensors API -> NMEA 0183 over TCP")
    p.add_argument("--host", default="0.0.0.0", help="adres nasluchu (domyslnie 0.0.0.0)")
    p.add_argument("--port", type=int, default=10110, help="port TCP (domyslnie 10110)")
    p.add_argument("--rate", type=float, default=5.0, help="czestotliwosc wysylania, Hz (domyslnie 5)")
    p.add_argument("--sentences", default="gps,zda,vtg,hdg,att",
                   help="lista: gps,zda,vtg,hdg,att (domyslnie wszystkie)")
    p.add_argument("--sim-as-real", action="store_true",
                   help="w trybie symulacji/GPX podawaj dane jako zwykly fix GPS "
                        "(GGA 1, tryb A) zamiast oznaczac je jako symulacje (GGA 8, tryb S). "
                        "Potrzebne, gdy ploter ignoruje dane oznaczone jako symulator")
    p.add_argument("--gps-repeat", action="store_true",
                   help="powtarzaj ostatni fix z czestotliwoscia --rate; domyslnie kazdy "
                        "fix jest wysylany raz, z wlasnym czasem z odbiornika")
    p.add_argument("--offset", type=float, default=0.0,
                   help="korekta kursu w stopniach dodawana do odczytu kompasu (montaz/dewiacja)")
    p.add_argument("--variation", type=float, default=None,
                   help="deklinacja magnetyczna w stopniach (E dodatnia), uzyta gdy Windows nie podaje kursu rzeczywistego")
    p.add_argument("--stale", type=float, default=5.0,
                   help="po ilu sekundach bez odczytu przestac wysylac dane (domyslnie 5)")
    p.add_argument("--simulate", action="store_true", help="dane testowe zamiast czujnikow")
    p.add_argument("--gpx", metavar="PLIK",
                   help="odtwarzaj trase z pliku GPX (track lub route) zamiast czujnikow; "
                        "wlacza tryb symulacji")
    p.add_argument("--gpx-speed", type=float, default=1.0, metavar="X",
                   help="mnoznik tempa odtwarzania: 1 = czas rzeczywisty, 10 = 10x szybciej")
    p.add_argument("--gpx-knots", type=float, default=5.0, metavar="KN",
                   help="predkosc uzywana tylko gdy GPX nie ma znacznikow <time> (domyslnie 5)")
    p.add_argument("--gpx-loop", action="store_true",
                   help="po dojsciu do konca trasy zacznij od nowa")
    p.add_argument("--status-interval", type=float, default=60.0, metavar="S",
                   help="co ile sekund logowac wiersz stanu; 0 wylacza (domyslnie 60)")
    p.add_argument("--log-file", metavar="PLIK",
                   help="dopisuj diagnostyke takze do pliku (stderr zostaje)")
    p.add_argument("-v", "--verbose", action="store_true", help="drukuj zdania na stdout")
    p.add_argument("--version", action="version", version=f"sensors2nmea {__version__}")
    a = p.parse_args()
    a.sentences = {x.strip().lower() for x in a.sentences.split(",") if x.strip()}
    known = {"gps", "zda", "vtg", "hdg", "att"}
    bad = a.sentences - known
    if bad:
        p.error(f"--sentences: nieznane grupy {sorted(bad)}; dostepne: "
                f"{','.join(sorted(known))}")
    if not a.sentences:
        p.error("--sentences nie moze byc puste")
    if a.gpx_speed <= 0:
        p.error("--gpx-speed musi byc > 0")
    if a.rate <= 0:
        p.error("--rate musi byc > 0")
    if a.stale <= 0:
        p.error("--stale musi byc > 0")
    if not 1 <= a.port <= 65535:
        p.error("--port musi byc w zakresie 1-65535")
    # Odtworzona trasa to dane symulowane, wiec musi byc tak oznaczona w NMEA
    # (GGA 8 / RMC tryb S), a nie udawac fixa satelitarnego.
    if a.gpx:
        a.simulate = True
    return a


async def main():
    args = parse_args()

    log = make_log(args.log_file)
    log(f"sensors2nmea {__version__} startuje (Python {sys.version.split()[0]})")

    state = State()

    # Zrodlo danych wybierane raz; walidacja GPX PRZED zajeciem portu, zeby zly
    # plik nie zostawil po sobie nasluchujacego gniazda.
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

    server = Server(args, log)
    try:
        srv = await asyncio.start_server(server.handle, args.host, args.port)
    except OSError as e:
        # WSAEADDRINUSE trafia w errno (asyncio owija blad), nie w winerror;
        # 48/98 to odpowiedniki na BSD/Linux.
        hint = ""
        in_use = {10048, 48, 98}
        if e.errno in in_use or getattr(e, "winerror", None) in in_use:
            hint = (f"\n       port {args.port} jest zajety. Sprawdz czym:"
                    f"\n         Get-NetTCPConnection -LocalPort {args.port}"
                    f"\n       i podaj inny przez --port")
        raise Fatal(f"nie moge nasluchiwac na {args.host}:{args.port}: {e}{hint}")

    log(f"TCP: nasluch na {args.host}:{args.port}, {args.rate:g} Hz, "
        f"zdania: {','.join(sorted(args.sentences))}")
    if args.host == "0.0.0.0":
        log("TCP: 0.0.0.0 wystawia serwer na wszystkie interfejsy; w niezaufanej "
            "sieci uzyj --host 127.0.0.1", "WARN")

    tasks = [
        srv.serve_forever(),
        supervise("zrodlo danych", make_source, log),
        supervise("rozsylanie", lambda: server.broadcast_loop(state), log),
    ]
    if args.status_interval > 0:
        tasks.append(supervise("status", lambda: server.status_loop(state), log))

    async with srv:
        await asyncio.gather(*tasks)


def cli():
    """Punkt wejscia konsolowy (patrz pyproject.toml). Zwraca kod wyjscia."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nprzerwane przez uzytkownika", file=sys.stderr)
        return 130
    except Fatal as e:
        print(f"BLAD: {e}", file=sys.stderr)
        return 1
    except Exception:
        print("BLAD nieobsluzony:\n" + traceback.format_exc(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())