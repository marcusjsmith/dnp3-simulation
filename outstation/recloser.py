"""Recloser outstation state and DNP3 point simulation."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydnp3 import opendnp3
from dnp3demo.control_workflow_demo import MyOutStation

_log = logging.getLogger(__name__)

# Serialize all opendnp3 database writes — the C++ stack is not thread-safe.
_dnp3_lock = threading.Lock()


@dataclass
class RecloserState:
    """Thread-safe recloser process state."""

    voltage_kv: float = 12.0
    current_a: float = 185.0
    power_kw: float = 2.2
    breaker_closed: bool = True
    fault: bool = False
    last_command: str = "None"
    last_update: str = field(default_factory=lambda: _now())
    dnp3_connected: bool = False
    command_count: int = 0

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "voltage_kv": round(self.voltage_kv, 2),
                "current_a": round(self.current_a, 1),
                "power_kw": round(self.power_kw, 1),
                "breaker_closed": self.breaker_closed,
                "breaker_open": not self.breaker_closed,
                "fault": self.fault,
                "last_command": self.last_command,
                "last_update": self.last_update,
                "dnp3_connected": self.dnp3_connected,
                "command_count": self.command_count,
            }

    def trip(self, source: str = "DNP3") -> None:
        with self._lock:
            if not self.breaker_closed:
                return
            self.breaker_closed = False
            self.current_a = 0.0
            self.power_kw = 0.0
            self.last_command = f"TRIP ({source})"
            self.command_count += 1
            self.last_update = _now()

    def close(self, source: str = "DNP3") -> None:
        with self._lock:
            if self.breaker_closed:
                return
            self.breaker_closed = True
            self.current_a = random.uniform(160.0, 210.0)
            self.power_kw = self.voltage_kv * self.current_a * 0.95 / 1000.0
            self.last_command = f"CLOSE ({source})"
            self.command_count += 1
            self.last_update = _now()

    def tick(self) -> None:
        with self._lock:
            self.voltage_kv = max(11.2, min(12.8, self.voltage_kv + random.uniform(-0.05, 0.05)))
            if self.breaker_closed:
                self.current_a = max(120.0, min(260.0, self.current_a + random.uniform(-4.0, 4.0)))
                self.power_kw = self.voltage_kv * self.current_a * 0.95 / 1000.0
            else:
                self.current_a = 0.0
                self.power_kw = 0.0
            self.last_update = _now()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _push_state_to_dnp3(outstation: RecloserOutstation, snap: dict) -> None:
    """Apply all point updates under the DNP3 lock."""
    with _dnp3_lock:
        outstation.apply_update(opendnp3.Analog(value=float(snap["voltage_kv"])), 0)
        outstation.apply_update(opendnp3.Analog(value=float(snap["current_a"])), 1)
        outstation.apply_update(opendnp3.Analog(value=float(snap["power_kw"])), 2)
        outstation.apply_update(opendnp3.Binary(value=snap["breaker_closed"]), 0)
        outstation.apply_update(opendnp3.Binary(value=snap["breaker_open"]), 1)
        outstation.apply_update(opendnp3.Binary(value=snap["fault"]), 2)


class RecloserOutstation(MyOutStation):
    """DNP3 outstation with recloser-specific command handling."""

    def __init__(self, state: RecloserState, **kwargs):
        self.state = state
        super().__init__(**kwargs)

    def process_point_value(self, command_type, command, index, op_type):
        # Update process state only — DNP3 point sync happens on the dedicated sync thread.
        if command_type == "Operate" and index == 0:
            if isinstance(command, opendnp3.ControlRelayOutputBlock):
                if command.rawCode == 4:  # LATCH_OFF / trip
                    self.state.trip("DNP3 LATCH_OFF")
                elif command.rawCode == 3:  # LATCH_ON / close
                    self.state.close("DNP3 LATCH_ON")
        # Acknowledge the command to the master (must stay on this call path).
        with _dnp3_lock:
            super().process_point_value(command_type, command, index, op_type)


def dnp3_sync_loop(
    outstation: RecloserOutstation,
    state: RecloserState,
    stop_event: threading.Event,
) -> None:
    """Single thread owns all periodic state ticks and DNP3 database writes."""
    while not stop_event.is_set():
        state.tick()
        try:
            state.dnp3_connected = outstation.is_connected
        except Exception:
            state.dnp3_connected = False

        snap = state.snapshot()
        try:
            _push_state_to_dnp3(outstation, snap)
        except Exception as exc:
            _log.warning("DNP3 sync failed: %s", exc)

        stop_event.wait(1.0)


def start_outstation(
    state: RecloserState,
    host: str = "0.0.0.0",
    port: int = 20000,
) -> tuple[RecloserOutstation, threading.Event, threading.Thread]:
    outstation = RecloserOutstation(
        state=state,
        outstation_ip=host,
        port=port,
        master_id=2,
        outstation_id=1,
    )
    outstation.start()
    stop_event = threading.Event()
    thread = threading.Thread(
        target=dnp3_sync_loop,
        args=(outstation, state, stop_event),
        daemon=True,
        name="dnp3-sync",
    )
    thread.start()
    time.sleep(0.5)
    return outstation, stop_event, thread
