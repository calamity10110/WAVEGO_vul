"""System stats for the status card — CPU %, RAM %, CPU temperature.

Reads Linux /proc and /sys directly (Pi 5). Everything degrades to None on
anything else (dev boxes, macOS) — the status card just omits missing
numbers.
"""

from __future__ import annotations

from typing import Callable


def _default_read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="ascii") as fh:
            return fh.read()
    except OSError:
        return None


class SystemStats:

    def __init__(self, read_text: Callable[[str], str | None] | None = None):
        self._read = read_text or _default_read
        self._prev_idle = None
        self._prev_total = None

    @staticmethod
    def _parse_cpu(line: str) -> tuple[float, float]:
        fields = [float(f) for f in line.split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0.0)
        return idle, float(sum(fields))

    @staticmethod
    def _parse_meminfo(text: str) -> tuple[float, float] | None:
        total = available = None
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                total = float(line.split()[1])
            elif line.startswith("MemAvailable:"):
                available = float(line.split()[1])
        if not total:
            return None
        used_pct = 100.0 * (total - (available or 0.0)) / total
        return total, used_pct

    def poll(self) -> dict:
        """{'cpu_percent': float|None, 'ram_percent': float|None,
        'cpu_temp_c': float|None} — cpu_percent needs two polls to exist
        (it is a delta), the first call reports None for it."""
        out = {"cpu_percent": None, "ram_percent": None, "cpu_temp_c": None}

        cpu_line = self._read("/proc/stat")
        if cpu_line:
            idle, total = self._parse_cpu(cpu_line.splitlines()[0])
            if self._prev_total and total > self._prev_total:
                out["cpu_percent"] = round(
                    100.0 * (1.0 - (idle - self._prev_idle)
                             / (total - self._prev_total)), 1)
            self._prev_idle, self._prev_total = idle, total

        mem = self._read("/proc/meminfo")
        if mem:
            parsed = self._parse_meminfo(mem)
            if parsed:
                out["ram_percent"] = round(parsed[1], 1)

        temp = self._read("/sys/class/thermal/thermal_zone0/temp")
        if temp:
            try:
                out["cpu_temp_c"] = round(float(temp.strip()) / 1000.0, 1)
            except ValueError:
                pass
        return out

    @staticmethod
    def format(stats: dict) -> str:
        """'CPU 34%  RAM 41%  62°C' — omits whatever is missing."""
        parts = []
        if stats.get("cpu_percent") is not None:
            parts.append(f"CPU {stats['cpu_percent']:.0f}%")
        if stats.get("ram_percent") is not None:
            parts.append(f"RAM {stats['ram_percent']:.0f}%")
        if stats.get("cpu_temp_c") is not None:
            parts.append(f"{stats['cpu_temp_c']:.0f}°C")
        return "  ".join(parts)
