# Dockerfile for tide_bot.py
#
# BLE note: Bluetooth from inside a container needs access to the host's
# Bluetooth stack. The cleanest way is to run with:
#
#   docker run --rm --net=host --privileged \
#     -v /var/run/dbus:/var/run/dbus \
#     tide-bot
#
# --net=host and --privileged are required because BLE (via BlueZ/D-Bus)
# doesn't virtualize cleanly like a normal network port. If your MeshCore
# node instead exposes a serial-to-TCP bridge, switch the script to
# MeshCore.create_tcp(...) and you can drop --privileged and the D-Bus
# mount entirely — much simpler to containerize.

FROM python:3.12-slim

WORKDIR /app

# bluez client libs needed for BLE support via bleak on Linux
RUN apt-get update \
    && apt-get install -y --no-install-recommends bluez dbus \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tide_bot.py .

CMD ["python", "tide_bot.py"]
