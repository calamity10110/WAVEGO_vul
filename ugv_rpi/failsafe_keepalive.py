"""Host-side failsafe daemon — best-effort post-mortem stop, no reflash required.

The stock ESP32 firmware keeps executing the last T:111/T:1 gait command
forever. If the main app process dies mid-walk, nothing sends a stop. This
daemon closes most of that gap in software:

1. Polls the app's /api/status every second.
2. Remembers the last time the app reported a movement command
   (motion.last_move_s_ago).
3. If the app stops responding after having recently reported motion, the
   UART is now free (the dead app released it) — this daemon opens it
   directly and writes a stop command, then stands down until the app
   returns.

Conservative by design: if the app never came up, or the last known motion
is older than the grace window, it does nothing (the robot may be driven by
something else, or already stopped). It is best-effort: it cannot see
motion that was never reported, and it cannot act while a hung-but-alive
app still holds the serial device.

Run as a systemd service: sudo ./install_failsafe.sh
"""

import json
import logging
import time

import requests

LOG = logging.getLogger("wavego-failsafe")

POLL_INTERVAL_S = 1.0
DEAD_AFTER_FAILURES = 3
MOTION_WINDOW_S = 10.0
STOP_CMD = {"T": 111, "FB": 0, "LR": 0}


def is_pi5():
    try:
        with open('/proc/cpuinfo', 'r') as fh:
            return 'Raspberry Pi 5' in fh.read()
    except OSError:
        return False


def uart_device():
    return '/dev/ttyAMA0' if is_pi5() else '/dev/serial0'


def decide(now, last_motion_t, consecutive_failures, dead_after=DEAD_AFTER_FAILURES,
           motion_window_s=MOTION_WINDOW_S, already_stopped=False):
    """Pure decision: return 'stop', 'watch', or 'reset'.

    'stop'  — app is dead long enough AND motion was reported inside the window
    'reset' — poll succeeded; failure counter clears
    'watch' — keep waiting (failure counter stays armed)
    """
    if consecutive_failures == 0:
        return 'reset'
    if consecutive_failures < dead_after or already_stopped:
        return 'watch'
    if last_motion_t is None:
        return 'watch'
    if now - last_motion_t > motion_window_s:
        return 'watch'
    return 'stop'


def send_stop_direct(uart):
    import serial
    with serial.Serial(uart, 115200, timeout=1) as ser:
        for _ in range(3):
            ser.write((json.dumps(STOP_CMD) + '\n').encode('utf-8'))
            time.sleep(0.05)
    LOG.warning("failsafe fired: direct stop written to %s", uart)


def main(app_url="http://127.0.0.1:5000/api/status"):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [failsafe] %(levelname)s %(message)s")
    session = requests.Session()
    last_motion_t = None
    failures = 0
    stopped = False

    LOG.info("watching %s (stop window %.0fs, dead after %d failures)",
             app_url, MOTION_WINDOW_S, DEAD_AFTER_FAILURES)
    while True:
        now = time.time()
        try:
            d = session.get(app_url, timeout=1.5).json()
            age = (d.get('motion') or {}).get('last_move_s_ago')
            last_motion_t = now - age if age is not None else last_motion_t
            failures = 0
            if stopped:
                LOG.info("app is back — standing down")
                stopped = False
        except Exception:
            failures += 1

        verdict = decide(time.time(), last_motion_t, failures, already_stopped=stopped)
        if verdict == 'stop':
            try:
                send_stop_direct(uart_device())
            except Exception as e:
                LOG.error("direct stop failed (serial held by live-but-hung app?): %s", e)
            stopped = True
            last_motion_t = None
            failures = 0
        elif verdict == 'reset' and failures == 0:
            pass
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
