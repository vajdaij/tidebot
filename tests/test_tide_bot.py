"""Offline tests for tide_bot.py: fake NOAA responses and a fake MeshCore node.
No network or Bluetooth needed. Run: python -m unittest -v
"""
import asyncio
import importlib
import inspect
import logging
import os
import pkgutil
import sys
import types
import unittest
from types import SimpleNamespace as NS
from unittest import mock

# Use the real meshcore package when it's installed (CI installs it). If it's
# missing, stub it so the offline tests still run; the API-contract tests skip.
try:
    import meshcore  # noqa: F401
    HAVE_MESHCORE = True
except ImportError:
    if os.environ.get("REQUIRE_MESHCORE"):  # set in CI so these checks can't silently skip
        raise
    HAVE_MESHCORE = False
    stub = types.ModuleType("meshcore")
    stub.MeshCore = type("MeshCore", (), {})
    sys.modules["meshcore"] = stub

import tide_bot  # noqa: E402

logging.disable(logging.CRITICAL)  # keep test output readable

SAMPLE = {"predictions": [
    {"t": "2026-10-21 04:12", "v": "0.312", "type": "L"},
    {"t": "2026-10-21 10:31", "v": "4.871", "type": "H"},
    {"t": "2026-10-21 16:40", "v": "-0.104", "type": "L"},
    {"t": "2026-10-21 22:55", "v": "5.020", "type": "H"},
]}


def noaa(payload, status_error=None):
    resp = mock.Mock()
    resp.json.return_value = payload
    resp.raise_for_status.side_effect = status_error
    return resp


def run(coro):
    return asyncio.run(coro)


class FakeNode:
    """Stands in for a connected MeshCore node."""
    def __init__(self, reply="MSG_SENT"):
        self.sent, self.disconnected = [], False
        self.commands = self
        self._reply = reply

    async def send_chan_msg(self, chan, msg):
        self.sent.append((chan, msg))
        return NS(type=NS(name=self._reply), payload={"reason": "test"})

    async def disconnect(self):
        self.disconnected = True


class GetTidesTests(unittest.TestCase):
    @mock.patch("tide_bot.requests.get")
    def test_formats_predictions(self, get):
        get.return_value = noaa(SAMPLE)
        msg = tide_bot.get_tides()
        self.assertEqual(msg.splitlines(), [
            "Sunset Beach tides:",
            "Low 04:12  0.312ft",
            "High 10:31  4.871ft",
            "Low 16:40  -0.104ft",
            "High 22:55  5.020ft",
        ])

    @mock.patch("tide_bot.requests.get")
    def test_requests_configured_station_with_timeout(self, get):
        get.return_value = noaa(SAMPLE)
        tide_bot.get_tides()
        url = get.call_args.args[0]
        self.assertIn(f"station={tide_bot.STATION_ID}", url)
        self.assertIn("interval=hilo", url)
        self.assertEqual(get.call_args.kwargs["timeout"], tide_bot.NOAA_TIMEOUT_SEC)

    @mock.patch("tide_bot.requests.get")
    def test_noaa_error_payload_raises(self, get):
        get.return_value = noaa({"error": {"message": "No data was found."}})
        with self.assertRaisesRegex(RuntimeError, "no predictions"):
            tide_bot.get_tides()

    @mock.patch("tide_bot.requests.get")
    def test_empty_prediction_list_raises(self, get):
        get.return_value = noaa({"predictions": []})
        with self.assertRaisesRegex(RuntimeError, "empty"):
            tide_bot.get_tides()

    @mock.patch("tide_bot.requests.get")
    def test_http_error_raises(self, get):
        get.return_value = noaa(SAMPLE, status_error=tide_bot.requests.HTTPError("503"))
        with self.assertRaises(tide_bot.requests.HTTPError):
            tide_bot.get_tides()


@mock.patch("tide_bot.CONNECT_RETRY_DELAY_SEC", 0)
class ConnectTests(unittest.TestCase):
    def test_retries_then_connects(self):
        node = FakeNode()
        create = mock.AsyncMock(side_effect=[OSError("busy"), OSError("busy"), node])
        with mock.patch.object(tide_bot.MeshCore, "create_ble", create, create=True):
            self.assertIs(run(tide_bot.connect_with_retry()), node)
        self.assertEqual(create.await_count, 3)
        create.assert_awaited_with(tide_bot.BLE_ADDRESS)

    def test_gives_up_after_max_attempts(self):
        create = mock.AsyncMock(side_effect=OSError("no adapter"))
        with mock.patch.object(tide_bot.MeshCore, "create_ble", create, create=True):
            with self.assertRaisesRegex(RuntimeError, "Could not connect"):
                run(tide_bot.connect_with_retry())
        self.assertEqual(create.await_count, tide_bot.MAX_CONNECT_ATTEMPTS)


class SendTests(unittest.TestCase):
    def test_success(self):
        node = FakeNode()
        self.assertTrue(run(tide_bot.send_tides(node, "hi")))
        self.assertEqual(node.sent, [(tide_bot.CHANNEL_IDX, "hi")])

    def test_error_reply_is_failure(self):
        self.assertFalse(run(tide_bot.send_tides(FakeNode(reply="ERROR"), "hi")))


@mock.patch("tide_bot.CONNECT_RETRY_DELAY_SEC", 0)
class MainTests(unittest.TestCase):
    """End to end through main(): exit codes and cleanup."""

    def _main(self, node=None, create_error=None, payload=SAMPLE):
        create = mock.AsyncMock(side_effect=create_error, return_value=node)
        with mock.patch("tide_bot.requests.get", return_value=noaa(payload)), \
             mock.patch.object(tide_bot.MeshCore, "create_ble", create, create=True):
            return run(tide_bot.main()), create

    def test_happy_path_posts_and_disconnects(self):
        node = FakeNode()
        code, _ = self._main(node)
        self.assertEqual(code, 0)
        self.assertTrue(node.sent[0][1].startswith("Sunset Beach tides:"))
        self.assertTrue(node.disconnected)

    def test_noaa_failure_exits_1_without_touching_radio(self):
        code, create = self._main(FakeNode(), payload={"error": {}})
        self.assertEqual(code, 1)
        create.assert_not_awaited()

    def test_send_error_exits_1_and_still_disconnects(self):
        node = FakeNode(reply="ERROR")
        code, _ = self._main(node)
        self.assertEqual(code, 1)
        self.assertTrue(node.disconnected)

    def test_connect_failure_exits_1(self):
        code, _ = self._main(create_error=OSError("no adapter"))
        self.assertEqual(code, 1)


@unittest.skipUnless(HAVE_MESHCORE, "meshcore not installed")
class MeshCoreApiContractTests(unittest.TestCase):
    """The tests above use fakes, so these check the real library still has the
    names tide_bot.py relies on. They catch breaking meshcore updates."""

    def test_create_ble_and_disconnect_exist(self):
        from meshcore import MeshCore
        self.assertTrue(inspect.iscoroutinefunction(MeshCore.create_ble))
        self.assertTrue(callable(getattr(MeshCore, "disconnect", None)))

    def test_send_chan_msg_exists(self):
        found = []
        for info in pkgutil.walk_packages(meshcore.__path__, "meshcore."):
            try:
                mod = importlib.import_module(info.name)
            except Exception:
                continue
            for _, cls in inspect.getmembers(mod, inspect.isclass):
                if cls.__module__ == mod.__name__ and hasattr(cls, "send_chan_msg"):
                    found.append(cls)
        self.assertTrue(found, "no meshcore class defines send_chan_msg anymore")
        params = inspect.signature(found[0].send_chan_msg).parameters
        self.assertGreaterEqual(len([p for p in params if p != "self"]), 2)

    def test_error_event_type_exists(self):
        from meshcore import EventType
        self.assertIn("ERROR", EventType.__members__)


if __name__ == "__main__":
    unittest.main()
