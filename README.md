# Bike Computer

Raspberry Pi Zero 2 W bike computer: ILI9488 SPI TFT in **portrait**, gpsd,
three buttons, Bluetooth pairing for heart-rate straps and headphones, local
music playback, optional Spotify, and Strava upload.

## Controls

Three buttons, and one rule that makes the rest predictable: **hold SELECT
always means "back"**, and it is the only way out of a menu.

| | Data screens (ride / map / detail) | Menus and lists |
|---|---|---|
| **UP** | previous screen | move up (auto-repeats when held) |
| **DOWN** | next screen | move down (auto-repeats when held) |
| **SELECT** | play / pause music | activate the highlighted row |
| **SELECT (hold)** | open the menu | back one level |

Menu tree:

```
Menu
├─ Music ........ play/pause, skip, volume, shuffle, library, source
├─ Bluetooth .... power, paired devices, add a device, auto-connect
│   └─ device ... connect, use as heart rate, use as audio out, forget
├─ Ride ......... auto-pause, GPS filter, reset trip, upload to Strava
├─ Display ...... units (imperial/metric), start screen
└─ System ....... GPS status, sensors, Spotify link, shut down, reboot
```

## Hardware

| Pin (BCM) | Wire |
|-----------|------|
| GPIO 25   | Display DC (Data/Command) |
| GPIO 27   | Display RST (Reset) |
| GPIO 17   | Button UP |
| GPIO 5    | Button SELECT |
| GPIO 6    | Button DOWN |
| SPI0 CS0  | Display CS (CE0) |
| SPI0 MOSI | Display MOSI |
| SPI0 CLK  | Display SCK |

Each button goes between its GPIO and **GND** — the internal pull-ups are
enabled in software, so no external resistors are needed. The pins avoid SPI
(7–11), the display's DC/RST, and the GPS UART (14/15).

GPS: L76K on `/dev/serial0` → gpsd.

## OS setup

### 1. `/boot/firmware/config.txt`

```ini
dtparam=spi=on
dtoverlay=spi0-1cs    # exposes /dev/spidev0.0
```

Reboot and confirm `ls /dev/spidev0.0`.

### 2. System packages

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv python3-gps gpsd gpsd-clients \
                    libgpiod2 python3-libgpiod fonts-dejavu \
                    mpv bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth
```

`mpv` plays the local library; `pipewire` + `libspa-0.2-bluetooth` provide A2DP
so Bluetooth headphones become the default audio sink automatically once
connected.

### 3. gpsd

Edit `/etc/default/gpsd`:

```
DEVICES="/dev/serial0"
GPSD_OPTIONS="-n"
```

```bash
sudo systemctl enable --now gpsd
cgps -s          # test
```

### 4. Install the app

```bash
sudo mkdir -p /opt/bikecomputer /var/bikecomputer/rides /var/bikecomputer/music
sudo chown -R caden:caden /opt/bikecomputer /var/bikecomputer

cd /opt/bikecomputer
git clone <this-repo> .

python3 -m venv venv
venv/bin/pip install -e ".[dev]"
```

### 5. Increase the SPI buffer (optional, improves throughput)

```bash
echo 'options spidev bufsiz=65536' | sudo tee /etc/modprobe.d/spidev.conf
sudo reboot
```

### 6. Display orientation

The panel is driven in portrait (320×480), with `MADCTL = 0x40` in
`src/bikecomputer/config.py`.

Do not try to derive this value arithmetically. Whether a given setting comes
out rotated or mirrored depends on how the glass is bonded to the controller,
which is not in the datasheet — on this module the bonding is mirrored, so the
value that looks wrong on paper is the correct one. If you change panels, ask
the hardware instead:

```bash
sudo systemctl stop bikecomputer
venv/bin/python tools/orientation.py
```

It cycles the four portrait candidates (`0x00`, `0x40`, `0x80`, `0xC0`) and
shows a test pattern built around a letter F — the one glyph where "upside
down" and "mirrored" can't be mistaken for each other. Pick the value where the
F reads normally, the red **TL** block is top-left, and the arrow points up.

Setting the `MV` bit (`0x20`) returns to landscape, which the UI is no longer
laid out for.

## Music

### Local files

Copy anything mpv can play (`.mp3 .m4a .flac .ogg .opus .wav .aac`) into
`/var/bikecomputer/music`. Subfolders are scanned too.

```bash
scp *.mp3 caden@bikecomputer.local:/var/bikecomputer/music/
```

Then **Menu → Music → Rescan library**. Track and artist names come from the
file's tags via `mutagen`, falling back to the filename.

The whole library is handed to mpv as one playlist, so playback rolls on by
itself between tracks; skip is a playlist seek.

### Bluetooth headphones

**Menu → Bluetooth → Add a device**, put the headphones in pairing mode, pick
them from the list. They are trusted automatically, so they reconnect on their
own afterwards, and a pair of headphones is assigned as the audio output
without asking. PipeWire routes mpv to them with no further configuration.

### Spotify (requires Premium)

Spotify has no local control protocol, so the app drives playback through the
Web API. **Skip and pause therefore always need a data connection on the Pi** —
tether it to your phone's hotspot. There are two ways to arrange the audio:

**Phone plays, Pi remotes (recommended).** Pair the headphones to your *phone*,
let the phone stream, and use the bike computer as a remote and now-playing
display. The phone has the better antenna and the bigger battery, and the audio
never depends on the Pi keeping up. Nothing to install beyond the account link
in step 1.

**Pi plays.** Pair the headphones to the *Pi* and let it stream directly via
`librespot`. Costs more battery and data on the Pi, and needs steps 2-3.

Either way, **Menu -> Music -> Play on...** lists every Spotify Connect device
on your account - phone, Pi, home speakers - and moves playback to the one you
pick.

#### 1. Link the account (both modes)

Create an app at <https://developer.spotify.com/dashboard>, add
`http://127.0.0.1:8766/callback` as a Redirect URI, and put the Client ID in
`SPOTIFY_CLIENT_ID` in `config.py`. No client secret is needed - the flow is
Authorization Code with PKCE, and only a refresh token is stored on the Pi
(mode 0600).

```bash
/opt/bikecomputer/venv/bin/python -m bikecomputer.music.spotify auth
```

Open the printed URL on any device, approve, done. Then on the bike:
**Menu -> Music -> Source -> Spotify**.

#### 2. Install librespot (Pi-plays mode only)

```bash
sudo apt install -y cargo pkg-config libasound2-dev
cargo install librespot        # ~20 min on a Zero 2 W; cross-compile if you can
sudo install -m755 ~/.cargo/bin/librespot /usr/local/bin/
```

#### 3. Sign librespot in, once

Easy to skip, and the failure is confusing: without cached credentials
librespot only announces itself over **zeroconf**, so it is visible to phones
on the same Wi-Fi but **invisible to the Web API** - the Pi simply never
appears under "Play on...".

```bash
mkdir -p /var/bikecomputer/librespot && chmod 700 /var/bikecomputer/librespot
librespot -n BikeComputer -c /var/bikecomputer/librespot -j
```

`-j` (`--enable-oauth`) prints a URL; approve it and the credential blob is
cached. Ctrl-C once it reports a successful login - the app launches librespot
itself from then on, and skips launching it entirely if that blob is missing,
logging why.

If audio is silent afterwards, librespot picked the wrong output. Run
`librespot --backend ?` to list what it was built with and set
`SPOTIFY_BACKEND` in `config.py` (`"pulseaudio"` routes through pipewire-pulse).
`SPOTIFY_LIBRESPOT_ARGS` takes any extra flags.

## Heart rate and cadence

Pair the strap under **Menu → Bluetooth → Add a device**. Anything advertising
the standard Heart Rate service (0x180D) is recognised and assigned as the
heart-rate sensor automatically; cadence sensors using the CSC profile (0x1816)
are read too.

Readings are subscribed through bluetoothd's own GATT connection, so a strap
that drops out mid-ride and reconnects starts reporting again with no
intervention. A reading that goes quiet for more than 10 seconds blanks rather
than freezing on a stale number.

## Strava OAuth

1. Create a Strava API app at <https://www.strava.com/settings/api>, with
   `Authorization Callback Domain` set to `localhost`.
2. Put your Client ID in `config.py` (`STRAVA_CLIENT_ID`).
3. Store the client secret (never commit it):
   ```bash
   sudo mkdir -p /etc/bikecomputer
   echo 'STRAVA_CLIENT_SECRET=your_secret' | sudo tee /etc/bikecomputer/env
   sudo chmod 600 /etc/bikecomputer/env
   ```
4. Run the OAuth flow:
   ```bash
   STRAVA_CLIENT_SECRET=your_secret \
     /opt/bikecomputer/venv/bin/python -m bikecomputer.upload auth
   ```

Tokens land in `/var/bikecomputer/strava_tokens.json` and auto-refresh. Pending
rides upload at startup, or on demand from **Menu → Ride → Upload rides**.

## systemd service

```bash
sudo cp systemd/bikecomputer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bikecomputer

journalctl -u bikecomputer -f
```

Shut down and reboot from **Menu → System** flush the GPX file first, so a
ride is never lost to a hard power cut.

## Developing without the Pi

Every screen renders to PNG on any machine — no SPI, GPIO or D-Bus required:

```bash
python tools/preview.py            # writes preview/*.png + contact_sheet.png
```

Catching a layout mistake here is much cheaper than deploying and squinting at
the bike. Unit tests cover ride maths, settings persistence, button decoding
(short/long/repeat/debounce) and menu navigation:

```bash
python -m pytest tests/ -v
```

Other standalone checks:

```bash
# GPS parsing (prints fixes)
python -c "
import asyncio, logging
logging.basicConfig(level=logging.DEBUG)
from src.bikecomputer.gps import GpsClient
async def main():
    g = GpsClient(); await g.start()
    for _ in range(5):
        print(await asyncio.wait_for(g.queue.get(), 10))
asyncio.run(main())
"

# Display (red, green, blue)
python -c "
import time
from src.bikecomputer.display import Display
d = Display(); d.init()
for colour in [(255,0,0),(0,255,0),(0,0,255)]:
    d.fill(colour); time.sleep(1)
d.close()
"

# Bluetooth scan, no UI
python -c "
import asyncio
from src.bikecomputer.bluetooth import BluetoothManager
async def main():
    bt = BluetoothManager()
    await bt.start(); await bt.start_scan()
    await asyncio.sleep(8)
    for d in await bt.devices():
        print(f'{d.address}  {d.kind_label:12} {d.display_name}')
    await bt.stop()
asyncio.run(main())
"
```

## Route file

Place a GPX file at `/var/bikecomputer/rides/route.gpx` before starting. The map
screen draws it as an orange polyline and shows a "Turn ahead" banner within
100 m of a significant heading change.

## File layout

```
/opt/bikecomputer/                app source + venv
/opt/bikecomputer/neohio.mbtiles  vector tile basemap
/var/bikecomputer/
  rides/                          GPX ride files (*.gpx)
  rides/route.gpx                 optional pre-loaded route
  music/                          your audio files
  librespot/                      librespot credential + cache dir (0700)
  settings.json                   on-screen preferences (auto-managed)
  strava_tokens.json              Strava OAuth tokens
  spotify_tokens.json             Spotify refresh token (0600)
/etc/bikecomputer/env             STRAVA_CLIENT_SECRET (root-owned, 600)
```

## Module map

| Module | Responsibility |
|---|---|
| `app.py` | asyncio loop: GPS, buttons, render, housekeeping |
| `config.py` | pins, paths, colours, first-boot defaults |
| `settings.py` | rider preferences, persisted atomically to JSON |
| `buttons.py` | 3 GPIO buttons → short/long/repeat events |
| `display.py` | ILI9488 SPI driver, dirty-rectangle blits |
| `gps.py` / `ride.py` / `logger.py` | fixes, ride maths (SI), GPX logging |
| `bluetooth.py` | BlueZ over D-Bus: scan, pair, connect, forget |
| `sensors.py` | BLE heart-rate and cadence notifications |
| `music/` | mpv local playback, Spotify Connect, source routing |
| `units.py` | SI → imperial/metric at render time only |
| `ui/` | theme primitives, navigation stack, views |
