# Bike Computer

Raspberry Pi Zero 2 W bike computer with ILI9488 SPI TFT, gpsd, and Strava upload.

## Hardware

| Pin (BCM) | Wire |
|-----------|------|
| GPIO 25   | DC (Data/Command) |
| GPIO 27   | RST (Reset) |
| GPIO 17   | Button (GND on press) |
| SPI0 CS0  | Display CS (CE0) |
| SPI0 MOSI | Display MOSI |
| SPI0 CLK  | Display SCK |

GPS: L76K on `/dev/serial0` → gpsd.

## OS setup

### 1. Fix `/boot/firmware/config.txt`

Remove the failing fbtft line and replace with a plain spidev entry:

```ini
# Remove this:
# dtoverlay=fbtft,spi0-0,ili9488,...

# Keep:
dtparam=spi=on
dtoverlay=spi0-1cs    # exposes /dev/spidev0.0
```

Reboot and confirm:

```bash
ls /dev/spidev0.0   # should exist
```

### 2. System packages

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv python3-gps gpsd gpsd-clients \
                   libgpiod2 python3-libgpiod fonts-dejavu
```

### 3. gpsd

Edit `/etc/default/gpsd`:

```
DEVICES="/dev/serial0"
GPSD_OPTIONS="-n"
```

```bash
sudo systemctl enable gpsd
sudo systemctl start gpsd
# Test:
cgps -s
```

### 4. Install the app

```bash
sudo mkdir -p /opt/bikecomputer /var/bikecomputer/rides
sudo chown caden:caden /opt/bikecomputer /var/bikecomputer

cd /opt/bikecomputer
git clone <this-repo> .

python3 -m venv venv
venv/bin/pip install -e ".[dev]"
# or:
venv/bin/pip install -r requirements.txt
venv/bin/pip install -e .
```

### 5. Increase SPI buffer (optional, improves throughput)

```bash
echo 'options spidev bufsiz=65536' | sudo tee /etc/modprobe.d/spidev.conf
sudo reboot
```

### 6. Display orientation

If the image is rotated or colours are swapped, adjust `MADCTL` in `src/bikecomputer/config.py`:

| MADCTL | Effect |
|--------|--------|
| `0x28` | Landscape, BGR |
| `0x48` | Landscape mirror-X, BGR |
| `0x68` | Landscape mirror-X + mirror-Y, BGR |
| `0xE8` | Landscape 180°, BGR |

## Strava OAuth

### First-time setup

1. Create a Strava API app at <https://www.strava.com/settings/api>.
   - Set `Authorization Callback Domain` to `localhost`.
2. Set your Client ID in `config.py` (`STRAVA_CLIENT_ID`).
3. Store the client secret (never commit it):
   ```bash
   sudo mkdir -p /etc/bikecomputer
   echo 'STRAVA_CLIENT_SECRET=your_secret' | sudo tee /etc/bikecomputer/env
   sudo chmod 600 /etc/bikecomputer/env
   ```
4. Run the OAuth flow (Pi must have a browser or you must visit from another machine):
   ```bash
   STRAVA_CLIENT_SECRET=your_secret \
     /opt/bikecomputer/venv/bin/python -m bikecomputer.upload auth
   ```
   Open the printed URL, authorise the app, and tokens are saved automatically.

Tokens are stored in `/var/bikecomputer/strava_tokens.json` and auto-refreshed.

## systemd service

```bash
sudo cp systemd/bikecomputer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bikecomputer
sudo systemctl start bikecomputer

# Logs:
journalctl -u bikecomputer -f
```

## Running modules standalone (for testing)

```bash
# Test GPS parsing (prints fixes):
python -c "
import asyncio, logging
logging.basicConfig(level=logging.DEBUG)
from src.bikecomputer.gps import GpsClient
async def main():
    g = GpsClient()
    await g.start()
    for _ in range(5):
        fix = await asyncio.wait_for(g.queue.get(), 10)
        print(fix)
asyncio.run(main())
"

# Test display (fills screen red then green then blue):
python -c "
from src.bikecomputer.display import Display
from PIL import Image
d = Display()
d.init()
for colour in [(255,0,0),(0,255,0),(0,0,255)]:
    d.fill(colour)
    import time; time.sleep(1)
d.close()
"

# Test map rendering:
python -c "
from src.bikecomputer.mapview import MapView
mv = MapView()
if mv.available:
    img = mv.render_around(40.0, -83.0, 480, 300, zoom=14)
    img.save('/tmp/map_test.png')
    print('Saved /tmp/map_test.png')
"

# Run unit tests (no hardware):
python -m pytest tests/ -v
```

## Route file

Place a GPX file at `/var/bikecomputer/rides/route.gpx` before starting.
The map screen will draw the route as an orange polyline and show a
"Turn ahead" banner when within 100 m of a significant heading change.

## File layout

```
/opt/bikecomputer/      app source + venv
/var/bikecomputer/
  rides/                GPX ride files (*.gpx)
  rides/route.gpx       optional pre-loaded route
  strava_tokens.json    OAuth tokens (auto-managed)
/etc/bikecomputer/env   STRAVA_CLIENT_SECRET (root-owned, 600)
/opt/bikecomputer/ohio.mbtiles  vector tile basemap
```
