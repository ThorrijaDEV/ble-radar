"""Real BLE scanning via bleak (BlueZ D-Bus).

bleak is imported lazily so the rest of the tool (demo, replay, history,
export) works on systems without the package or without Bluetooth.

bleak 3.x is async; we run its event loop on a dedicated thread and hand
observations to the caller through the (thread-safe) callback. The D-Bus
connection stays open for the whole session — discovery dies the moment the
owning process exits, so we never spawn short-lived helpers.
"""

from __future__ import annotations

import asyncio
import threading

from ble_radar.scan import BaseScanner, Observation, ScannerError


class BleakScannerBackend(BaseScanner):
    """One long-lived discovery session on the default powered adapter.

    Design notes (see DESIGN.md §3.2):
    - a dedicated thread owns the asyncio loop; start()/stop() are synchronous
      and join it cleanly
    - ``Transport=le`` filter is required on LE-only controllers
    - we do not power the adapter on; if none is powered we raise ScannerError
    """

    def __init__(self, on_observation, clock=None):
        super().__init__(on_observation, clock)
        self._scanner = None
        self._loop = None
        self._thread = None
        self._started = None
        self._error = []

    def start(self):
        try:
            from bleak import BleakScanner  # noqa: F401
        except ImportError:
            raise ScannerError(
                "bleak is not installed (pip install bleak); "
                "use `ble-radar demo` for the synthetic radar"
            )

        if not self._is_adapter_available():
            raise ScannerError(
                "no powered Bluetooth adapter found — enable Bluetooth "
                "(bluetoothctl power on) and try again"
            )

        self._error = []
        self._started = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="bleak-scan", daemon=True
        )
        self._thread.start()
        if not self._started.wait(timeout=15):
            self.stop()
            raise ScannerError("timed out starting BLE discovery")
        if self._error:
            self.stop()
            raise ScannerError(f"failed to start BLE discovery: {self._error[0]}")
        self._running = True

    def _run(self):
        try:
            from bleak import BleakScanner
        except Exception as e:  # pragma: no cover - import already checked
            self._error.append(str(e))
            self._started.set()
            return
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._scanner = BleakScanner(detection_callback=self._on_detection)
            self._loop.run_until_complete(self._scanner.start())
        except Exception as e:
            self._error.append(str(e))
            self._started.set()
            return
        self._started.set()
        self._loop.run_forever()

    def _is_adapter_available(self) -> bool:
        """Best-effort check that at least one adapter is powered."""
        try:
            from bleak.backends.bluezdbus.manager import get_manager
        except Exception:
            return True  # let the scan itself decide
        try:
            manager = get_manager()
            if manager is None:
                return True
            return any(
                "central" in (props.get("Roles") or [])
                and props.get("Powered") is True
                for props in manager.adapters.values()
            )
        except Exception:
            return True

    def _on_detection(self, device, advertisement_data) -> None:
        try:
            address = device.address
        except Exception:
            return
        rssi = getattr(advertisement_data, "rssi", None)
        name = getattr(advertisement_data, "local_name", None) or device.name
        mfg = bool(getattr(advertisement_data, "manufacturer_data", None))
        uuids = tuple(getattr(advertisement_data, "service_uuids", ()) or ())
        self._emit(
            Observation(
                address=address,
                name=name,
                rssi=rssi,
                seen_at=self._clock(),
                backend="bleak",
                mfg_present=mfg,
                service_uuids=uuids,
            )
        )

    def stop(self):
        self._running = False
        if self._loop is not None and self._scanner is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._scanner.stop(), self._loop
                )
                future.result(timeout=5)
            except Exception:
                pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._scanner = None
        self._loop = None
