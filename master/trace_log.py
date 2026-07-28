"""DNP3 protocol trace capture — Wireshark-style message log."""

from __future__ import annotations

import re
import threading
from collections import deque
from datetime import datetime, timezone

from pydnp3 import asiodnp3, opendnp3, openpal
from dnp3_python.dnp3station.station_utils import SOEHandler

HEX_BYTE_RE = re.compile(r"\b([0-9A-Fa-f]{2})\b")
# App-level + link summaries; avoid logging every raw byte fragment from ALL_COMMS.
COMMS_LOG_LEVEL = opendnp3.levels.NORMAL | opendnp3.levels.ALL_APP_COMMS


class TraceBuffer:
    """Thread-safe ring buffer of parsed trace entries."""

    def __init__(self, maxlen: int = 500):
        self._entries: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    def add(
        self,
        direction: str,
        layer: str,
        summary: str,
        detail: str = "",
        hex_data: str = "",
        source: str = "stack",
    ) -> int:
        if not self._lock.acquire(blocking=False):
            return self._seq
        try:
            self._seq += 1
            entry = {
                "id": self._seq,
                "time": _format_time(),
                "dir": direction,
                "layer": layer,
                "summary": summary,
                "detail": detail,
                "hex": hex_data,
                "source": source,
            }
            self._entries.append(entry)
            return self._seq
        finally:
            self._lock.release()

    def add_from_log(self, location: str, message: str, logger_id: str = "") -> None:
        direction, layer, summary, hex_data = _parse_stack_message(location, message)
        # Drop high-volume low-level hex fragments to avoid blocking the ASIO thread.
        if layer == "INFO" and hex_data and len(message.strip()) < 40:
            return
        detail = message
        if logger_id:
            detail = f"[{logger_id}] {message}"
        self.add(direction, layer, summary, detail=detail, hex_data=hex_data, source="opendnp3")

    def since(self, last_id: int = 0) -> list[dict]:
        with self._lock:
            if last_id <= 0:
                return list(self._entries)
            return [e for e in self._entries if e["id"] > last_id]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._entries)


def _format_time() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def _extract_hex(message: str) -> str:
    bytes_found = HEX_BYTE_RE.findall(message)
    if len(bytes_found) >= 4:
        return " ".join(bytes_found)
    return ""


def _parse_stack_message(location: str, message: str) -> tuple[str, str, str, str]:
    loc = (location or "").rsplit("/", 1)[-1].lower()
    msg = message.strip()
    lower = msg.lower()

    direction = "INFO"
    if re.search(r"\btx\b", lower) or "->" in msg or "sending" in lower:
        direction = "TX"
    elif re.search(r"\brx\b", lower) or "<-" in msg or "received" in lower or "recv" in lower:
        direction = "RX"
    elif "source: 2" in lower and "dest: 1" in lower:
        direction = "TX"
    elif "source: 1" in lower and "dest: 2" in lower:
        direction = "RX"

    layer = "INFO"
    if "link" in loc or lower.startswith("function:") or "link layer" in lower:
        layer = "LINK"
    elif "transport" in loc or "transpt" in loc or lower.startswith("fir:"):
        layer = "TRANSPT"
    elif "app" in loc or (lower.startswith("fir:") and "func:" in lower):
        layer = "APP"
    elif "func:" in lower and ("read" in lower or "response" in lower or "confirm" in lower):
        layer = "APP"
    elif "tcp" in loc or "tcp" in lower or "client" in loc or "server" in loc:
        layer = "TCP"

    summary = msg if len(msg) <= 120 else msg[:117] + "..."
    hex_data = _extract_hex(msg)
    if hex_data and len(summary) < 20:
        summary = f"Frame data ({len(hex_data.split())} bytes)"
    return direction, layer, summary, hex_data


class TraceLogger(openpal.ILogHandler):
    """Captures opendnp3 stack log output into the trace buffer (non-blocking)."""

    def __init__(self, buffer: TraceBuffer):
        super().__init__()
        self.buffer = buffer

    def Log(self, entry) -> None:
        try:
            self.buffer.add_from_log(
                entry.location or "",
                entry.message or "",
                entry.loggerid or "",
            )
        except Exception:
            pass


class TraceChannelListener(asiodnp3.IChannelListener):
    """Logs TCP channel state transitions."""

    def __init__(self, buffer: TraceBuffer):
        super().__init__()
        self.buffer = buffer

    def OnStateChange(self, state) -> None:
        label = opendnp3.ChannelStateToString(state)
        self.buffer.add("INFO", "TCP", f"Channel state → {label}")


class TracingSOEHandler(SOEHandler):
    """Logs application-layer responses from the outstation."""

    def __init__(self, buffer: TraceBuffer, **kwargs):
        super().__init__(**kwargs)
        self.trace = buffer
        self.logger.setLevel(100)  # suppress noisy SOE stdout logging

    def Process(self, info, values, *args, **kwargs):
        try:
            header = str(getattr(info, "header", info))
            self.trace.add(
                "RX",
                "APP",
                f"Response data — {header[:80]}",
                detail=header[:300],
            )
        except Exception:
            pass
        return super().Process(info, values, *args, **kwargs)


trace_buffer = TraceBuffer()


def log_poll_request(group: int, variation: int) -> None:
    trace_buffer.add(
        "TX",
        "APP",
        f"Read request — Group {group} Var {variation}",
        detail=f"Master → Outstation 1 | Class 0 integrity read G{group}V{variation}",
    )


def log_poll_response(group: int, variation: int, result) -> None:
    detail = str(result) if result else "(empty)"
    trace_buffer.add(
        "RX",
        "APP",
        f"Read response — Group {group} Var {variation}",
        detail=detail[:500],
    )


def log_command(code_name: str, index: int) -> None:
    trace_buffer.add(
        "TX",
        "APP",
        f"Direct Operate — BO-{index} {code_name}",
        detail=f"CROB Group 12 Var 1 | Index {index} | ControlCode.{code_name}",
    )


def log_command_result(result) -> None:
    try:
        summary = opendnp3.TaskCompletionToString(result.summary)
        trace_buffer.add("RX", "APP", f"Command result — {summary}")
    except Exception:
        trace_buffer.add("RX", "APP", "Command result received")
