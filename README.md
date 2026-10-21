# tidebot

Tide schedules via MeshCore. Fetches today's high/low tide predictions from
[NOAA CO-OPS](https://tidesandcurrents.noaa.gov/) and posts them to a MeshCore
channel through a companion node connected over Bluetooth LE.

Defaults to Sunset Beach Pier, NC (station `8659897`).

## Files

| File | Purpose |
| --- | --- |
| `tide_bot.py` | Fetches tides, connects to the node over BLE (with retries), posts to the channel |
| `requirements.txt` | Python dependencies (`meshcore`, `requests`) |
| `Dockerfile` | Container image for running the script |

## Configuration

Edit the constants at the top of `tide_bot.py`:

| Setting | Description |
| --- | --- |
| `STATION_ID` | NOAA CO-OPS station ID. Find yours at [tidesandcurrents.noaa.gov](https://tidesandcurrents.noaa.gov/tide_predictions.html) |
| `CHANNEL_IDX` | Index of the target channel on your node (Public is usually `0`) |
| `BLE_ADDRESS` | BLE address of your MeshCore companion node |

To find your BLE address and channel index, run `meshcore-cli` once; it scans
for devices and can list configured channels.

The channel must already exist on the node you connect to.

## Run directly

```bash
pip install -r requirements.txt
python tide_bot.py
```

Run it once by hand before scheduling it, so you can see the connection and send
logs.

## Run with Docker

```bash
docker build -t tide-bot .
docker run --rm --net=host --privileged \
  -v /var/run/dbus:/var/run/dbus \
  tide-bot
```

BLE from a container needs `--net=host`, `--privileged`, and the host's D-Bus
socket so BlueZ is reachable. The host must have Bluetooth working and BlueZ
running.

## Schedule with cron

The script runs once and exits, so schedule it on the host. Tide times shift
about 50 minutes per day, so posting a day's schedule each morning works well:

```cron
# Native
0 6,14 * * * /usr/bin/python3 /path/to/tide_bot.py >> /var/log/tide_bot.log 2>&1

# Docker
0 6,14 * * * docker run --rm --net=host --privileged -v /var/run/dbus:/var/run/dbus tide-bot >> /var/log/tide_bot.log 2>&1
```

## Notes

- Exit code is `0` on a successful send and `1` on any failure (NOAA fetch,
  BLE connect after 3 attempts, or send error), so cron wrappers and monitors
  can react.
- Predictions come from NOAA's `datagetter` API (`product=predictions`,
  `interval=hilo`, datum MLLW, local standard/daylight time).
- BLE can be flaky on Linux when unattended. If it becomes a problem, a
  serial-to-TCP bridge with `MeshCore.create_tcp(...)` is more reliable and
  removes the need for `--privileged` in Docker.
