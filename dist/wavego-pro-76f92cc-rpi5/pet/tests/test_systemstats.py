"""System stats tests — /proc parsing and graceful degradation, all on
fixture strings (no real /proc needed)."""

from wavego_pet.systemstats import SystemStats


CPU_A = "cpu  100 0 50 850 0 0 0 0 0 0\n"       # idle 850, total 1000
CPU_B = "cpu  180 0 60 860 0 0 0 0 0 0\n"       # idle 860, total 1100


def make_reader(files):
    return lambda path: files.get(path)


def test_cpu_percent_is_delta_between_polls():
    seq = {"current": CPU_A}
    def read(path):
        return seq["current"] if path == "/proc/stat" else None
    s = SystemStats(read_text=read)
    first = s.poll()
    assert first["cpu_percent"] is None          # no previous sample yet
    seq["current"] = CPU_B
    second = s.poll()
    # idle went 850->860 of a 10-tick window: 90% busy
    assert second["cpu_percent"] == 90.0


def test_ram_percent_from_meminfo():
    meminfo = "MemTotal:       8000000 kB\nMemFree:        2000000 kB\n" \
              "MemAvailable:   4000000 kB\n"
    s = SystemStats(read_text=make_reader({"/proc/meminfo": meminfo}))
    assert s.poll()["ram_percent"] == 50.0


def test_cpu_temp_millidegrees():
    s = SystemStats(read_text=make_reader(
        {"/sys/class/thermal/thermal_zone0/temp": "62500\n"}))
    assert s.poll()["cpu_temp_c"] == 62.5


def test_all_missing_degrades_to_nones():
    s = SystemStats(read_text=lambda path: None)
    assert s.poll() == {"cpu_percent": None, "ram_percent": None,
                        "cpu_temp_c": None}


def test_format_omits_missing_and_keeps_order():
    assert SystemStats.format({}) == ""
    assert SystemStats.format({"cpu_percent": 34.4, "ram_percent": 41.0,
                               "cpu_temp_c": 62.5}) == "CPU 34%  RAM 41%  62°C"
    assert SystemStats.format({"ram_percent": 41.0}) == "RAM 41%"
