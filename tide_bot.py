#!/usr/bin/env python3
"""
tide_bot.py

Fetches today's high/low tide predictions from NOAA CO-OPS for a given
station and posts them to a MeshCore channel over BLE.

Setup:
    pip install meshcore requests

Usage:
    Fill in BLE_ADDRESS and CHANNEL_IDX below, then run directly or via cron:
        0 6,14 * * * /usr/bin/python3 /path/to/tide_bot.py >> /var/log/tide_bot.log 2>&1
"""

import asyncio
import logging
import sys

import requests
from meshcore import MeshCore

# ---- Configuration ----------------------------------------------------
STATION_ID = "8659897"            # Sunset Beach, NC (NOAA CO-OPS)
CHANNEL_IDX = 1                   # Set to your target channel's index
BLE_ADDRESS = "AA:BB:CC:DD:EE:FF"  # Your companion node's BLE address

MAX_CONNECT_ATTEMPTS = 3
CONNECT_RETRY_DELAY_SEC = 10
NOAA_TIMEOUT_SEC = 10
# ------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tide_bot")


def get_tides() -> str:
    """Fetch today's hi/lo tide predictions and format them as a message."""
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        f"?station={STATION_ID}&product=predictions&datum=MLLW"
        "&time_zone=lst_ldt&units=english&format=json"
        "&date=today&interval=hilo"
    )
    resp = requests.get(url, timeout=NOAA_TIMEOUT_SEC)
    resp.raise_for_status()
    payload = resp.json()

    if "predictions" not in payload:
        # NOAA returns an "error" key on bad station IDs or outages
        raise RuntimeError(f"NOAA API returned no predictions: {payload}")

    label = {"H": "High", "L": "Low"}
    lines = [
        f"{label.get(p['type'], p['type'])} {p['t'][-5:]}  {p['v']}ft"
        for p in payload["predictions"]
    ]
    if not lines:
        raise RuntimeError("NOAA API returned an empty prediction list")

    return "Sunset Beach tides:\n" + "\n".join(lines)


async def connect_with_retry() -> MeshCore:
    """Connect to the companion node over BLE, retrying on failure."""
    last_err = None
    for attempt in range(1, MAX_CONNECT_ATTEMPTS + 1):
        try:
            log.info("Connecting to MeshCore node (attempt %d/%d)...", attempt, MAX_CONNECT_ATTEMPTS)
            mc = await MeshCore.create_ble(BLE_ADDRESS)
            log.info("Connected.")
            return mc
        except Exception as e:
            last_err = e
            log.warning("Connection attempt %d failed: %s", attempt, e)
            if attempt < MAX_CONNECT_ATTEMPTS:
                await asyncio.sleep(CONNECT_RETRY_DELAY_SEC)
    raise RuntimeError(f"Could not connect after {MAX_CONNECT_ATTEMPTS} attempts") from last_err


async def send_tides(mc: MeshCore, message: str) -> bool:
    """Send the tide message to the configured channel. Returns True on success."""
    result = await mc.commands.send_chan_msg(CHANNEL_IDX, message)
    if result.type.name == "ERROR":
        log.error("Failed to send message: %s", result.payload)
        return False
    log.info("Message sent successfully: %s", result.type)
    return True


async def main() -> int:
    try:
        message = get_tides()
    except Exception as e:
        log.error("Failed to fetch tide data: %s", e)
        return 1

    log.info("Prepared message:\n%s", message)

    mc = None
    try:
        mc = await connect_with_retry()
        ok = await send_tides(mc, message)
        return 0 if ok else 1
    except Exception as e:
        log.error("Fatal error: %s", e)
        return 1
    finally:
        if mc is not None:
            try:
                await mc.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
