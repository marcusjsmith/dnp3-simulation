"""DNP3 master station polling and command logic."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydnp3 import opendnp3
from dnp3demo.control_workflow_demo import MyMaster
from dnp3_python.dnp3station.station_utils import command_callback


@dataclass
class MasterState:
    """Thread-safe master polled data."""

    voltage_kv: float = 0.0
    current_a: float = 0.0
    power_kw: float = 0.0
    breaker_closed: bool = False
    breaker_open: bool = True
    fault: bool = False
    dnp3_connected: bool = False
    last_poll: str = ""
    last_command: str = "None"
    poll_count: int = 0
    command_result: str = ""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update_from_poll(self, analogs: dict, binaries: dict) -> None:
        with self._lock:
            self.voltage_kv = float(analogs.get(0, 0.0) or 0.0)
            self.current_a = float(analogs.get(1, 0.0) or 0.0)
            self.power_kw = float(analogs.get(2, 0.0) or 0.0)
            self.breaker_closed = bool(binaries.get(0, False))
            self.breaker_open = bool(binaries.get(1, not self.breaker_closed))
            self.fault = bool(binaries.get(2, False))
            self.last_poll = _now()
            self.poll_count += 1

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self.dnp3_connected = connected

    def set_command(self, label: str, result: str = "Sent") -> None:
        with self._lock:
            self.last_command = label
            self.command_result = result

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "voltage_kv": round(self.voltage_kv, 2),
                "current_a": round(self.current_a, 1),
                "power_kw": round(self.power_kw, 1),
                "breaker_closed": self.breaker_closed,
                "breaker_open": self.breaker_open,
                "fault": self.fault,
                "dnp3_connected": self.dnp3_connected,
                "last_poll": self.last_poll,
                "last_command": self.last_command,
                "command_result": self.command_result,
                "poll_count": self.poll_count,
            }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _extract_points(db_result) -> tuple[dict, dict]:
    analogs: dict = {}
    binaries: dict = {}
    if not db_result:
        return analogs, binaries
    for key, values in db_result.items():
        key_str = str(key)
        if "Group30" in key_str:
            analogs = {int(k): v for k, v in values.items()}
        elif "Group1" in key_str:
            binaries = {int(k): v for k, v in values.items()}
    return analogs, binaries


def polling_loop(master: MyMaster, state: MasterState, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        state.set_connected(master.is_connected)
        if master.is_connected:
            try:
                analog_result = master.get_db_by_group_variation(group=30, variation=6)
                binary_result = master.get_db_by_group_variation(group=1, variation=2)
                analogs, _ = _extract_points(analog_result)
                _, binaries = _extract_points(binary_result)
                if not analogs:
                    analog_result = master.get_db_by_group_variation(group=30, variation=1)
                    analogs, _ = _extract_points(analog_result)
                state.update_from_poll(analogs, binaries)
            except Exception:
                pass
        stop_event.wait(1.5)


def start_master(
    state: MasterState,
    outstation_host: str,
    port: int = 20000,
) -> tuple[MyMaster, threading.Event, threading.Thread]:
    master = MyMaster(
        outstation_ip=outstation_host,
        port=port,
        master_id=2,
        outstation_id=1,
    )
    master.start()

    # Wait for connection with retries
    for _ in range(30):
        if master.is_connected:
            break
        time.sleep(1)

    stop_event = threading.Event()
    thread = threading.Thread(
        target=polling_loop,
        args=(master, state, stop_event),
        daemon=True,
        name="dnp3-poll",
    )
    thread.start()
    return master, stop_event, thread


def send_trip_command(master: MyMaster, state: MasterState) -> None:
    cmd = opendnp3.ControlRelayOutputBlock(opendnp3.ControlCode.LATCH_OFF)
    master.send_direct_operate_command(cmd, 0, command_callback)
    state.set_command("OPEN / TRIP (BO-0 LATCH_OFF)")


def send_close_command(master: MyMaster, state: MasterState) -> None:
    cmd = opendnp3.ControlRelayOutputBlock(opendnp3.ControlCode.LATCH_ON)
    master.send_direct_operate_command(cmd, 0, command_callback)
    state.set_command("CLOSE (BO-0 LATCH_ON)")
