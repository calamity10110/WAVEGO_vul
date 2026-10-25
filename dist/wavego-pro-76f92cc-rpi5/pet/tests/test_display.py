"""Display module tests — DSI detection, graceful degradation, brain wiring.

No real LCD, pygame, or DRM involved: detection runs on fake directory
listings and the LCDDisplay fallback path runs wherever pygame is missing.
"""

from wavego_pet.display import LCDDisplay, NullDisplay, detect_dsi_display


def fake_listdir(names, error=None):
    def _listdir(path):
        if error:
            raise error
        return names
    return _listdir


# ------------------------------------------------------------- detection

def test_detects_dsi_display_on_either_connector():
    assert detect_dsi_display(
        fake_listdir(["card0-HDMI-A-1", "card1-DSI-1"])) == "card1-DSI-1"
    assert detect_dsi_display(
        fake_listdir(["card0-DSI-1"])) == "card0-DSI-1"     # DSI0
    assert detect_dsi_display(
        fake_listdir(["card2-DSI-1"])) == "card2-DSI-1"     # DSI1


def test_no_dsi_display():
    assert detect_dsi_display(
        fake_listdir(["card0-HDMI-A-1", "card1-Writeback-1"])) is None


def test_detection_survives_missing_sysfs():
    assert detect_dsi_display(
        fake_listdir([], error=OSError("no /sys on this box"))) is None


# --------------------------------------------------------- degradation

def test_null_display_is_inert():
    d = NullDisplay()
    d.start()
    d.update("idle", "scene", "extra")
    d.stop()
    assert d.active is False


def test_lcd_display_degrades_without_pygame(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pygame(name, *args, **kwargs):
        if name == "pygame":
            raise ImportError("pygame not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pygame)
    d = LCDDisplay()
    d.start()                       # must not raise
    assert d.active is False
    d.update("idle", "sees: nothing")   # no-op, no crash
    d.stop()


# ------------------------------------------------------- layout scaling

def test_layout_scales_with_display_size():
    small = LCDDisplay(width=480, height=480).layout()
    large = LCDDisplay(width=1024, height=600).layout()
    assert small["title"] < large["title"]
    assert small["row"] < large["row"]
    assert large["title"] == int(600 * 0.10)
    assert small["title"] == int(480 * 0.10)


def test_layout_floors_are_legible():
    tiny = LCDDisplay(width=100, height=100).layout()
    assert tiny["title"] >= 16
    assert tiny["text"] >= 11
    assert tiny["stats"] >= 11
