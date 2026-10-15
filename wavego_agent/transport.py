"""Command transport — the only path native JSON takes toward the ESP32.

The motion supervisor owns the Transport instance; nothing else in the agent
writes to it. MockTransport is the development default: it records the exact
bytes that would go down the wire so the whole pipeline is testable without
hardware. SerialTransport is the later drop-in.
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from typing import Callable


def encode_line(cmd: dict) -> bytes:
    """Exact wire format: compact JSON + newline (the firmware's serialCtrl
    frames commands by \\n)."""
    return (json.dumps(cmd, separators=(",", ":")) + "\n").encode("utf-8")


class Transport(ABC):

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def send(self, cmd: dict) -> None:
        """Write one native command dict to the wire."""


class MockTransport(Transport):
    """Records everything, sends nothing. `.sent` holds the dicts in order;
    `.wire` holds the exact encoded bytes per send."""

    def __init__(self):
        self._lock = threading.Lock()
        self.sent: list[dict] = []
        self.wire: list[bytes] = []
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def send(self, cmd: dict) -> None:
        line = encode_line(cmd)
        with self._lock:
            self.sent.append(cmd)
            self.wire.append(line)

    @property
    def wire_lines(self) -> list[str]:
        with self._lock:
            return [b.decode("utf-8") for b in self.wire]


def default_uart_device(is_pi5: Callable[[], bool] | None = None) -> str:
    """Same device selection as ugv_rpi (failsafe_keepalive.uart_device)."""
    if is_pi5 is None:
        def is_pi5() -> bool:
            try:
                with open("/proc/cpuinfo", "r") as fh:
                    return "Raspberry Pi 5" in fh.read()
            except OSError:
                return False
    return "/dev/ttyAMA0" if is_pi5() else "/dev/serial0"


class SerialTransport(Transport):
    """Real UART. pyserial is imported in open() so the package imports
    cleanly anywhere. One owner, one port, newline-framed JSON."""

    def __init__(self, device: str | None = None, baud: int = 115200):
        self._device = device
        self._baud = baud
        self._ser = None

    def open(self) -> None:
        import serial  # lazy: only a real transport needs pyserial

        device = self._device or default_uart_device()
        self._ser = serial.Serial(device, self._baud, timeout=1)

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def send(self, cmd: dict) -> None:
        if self._ser is None:
            raise RuntimeError("SerialTransport.send before open()")
        self._ser.write(encode_line(cmd))
