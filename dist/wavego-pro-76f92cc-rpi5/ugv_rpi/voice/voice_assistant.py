"""WAVEGO Pro voice assistant — persistent process, keeps the ASR model loaded.

Pipeline: USB mic (16 kHz mono) -> energy VAD with endpointing -> Moonshine
ONNX transcription -> phrase match against voice_config.yaml grammar ->
robot command via the ugv_rpi bypass API (/api/cmd, /api/cv).

Runs as a systemd service (install_voice.sh) next to the main app. The ASR
model is loaded exactly once at startup; per-utterance cost is inference only
(spawning per command costs ~3.2 s of Python+ONNX init — that is the failure
mode this process exists to avoid).

Movement commands (T:111 with nonzero FB/LR) are level commands: the gait
keeps walking until the next T:111. Every voice-triggered movement is
therefore armed with move_timeout_s — the robot stops itself if no follow-up
command arrives.
"""

import os
import re
import queue
import subprocess
import sys
import time

import numpy as np
import requests
import sounddevice as sd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "voice_config.yaml"), "r") as fh:
    CFG = yaml.safe_load(fh)

BLOCK_MS = 100


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + str(msg), flush=True)


def find_input_device(hint, fallback_index):
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and hint.lower() in dev["name"].lower():
            return idx
    dev_count = len(sd.query_devices())
    if 0 <= fallback_index < dev_count:
        return fallback_index
    return None


class Beeps:
    """Tiny generated WAV cues played through aplay; device is never held."""

    NAMES = {"ack": "beep_ack.wav", "ok": "beep_ok.wav", "err": "beep_err.wav"}

    def __init__(self, enabled, aplay_device):
        self.enabled = enabled
        self.aplay_device = aplay_device
        self.dir = os.path.join(HERE, "sounds")
        if enabled:
            self._ensure_files()

    def _write_tone(self, path, freqs, dur=0.08):
        import soundfile as sf
        parts = []
        sr = 16000
        for f in freqs:
            t = np.linspace(0, dur, int(sr * dur), endpoint=False)
            parts.append(0.4 * np.sin(2 * np.pi * f * t))
        tone = np.concatenate(parts).astype(np.float32)
        sf.write(path, tone, sr, subtype="PCM_16")

    def _ensure_files(self):
        os.makedirs(self.dir, exist_ok=True)
        specs = {"ack": [[880], [1175]], "ok": [[1318]], "err": [[220, 233, 220]]}
        try:
            for key, seq in specs.items():
                path = os.path.join(self.dir, self.NAMES[key])
                if not os.path.exists(path):
                    self._write_tone(path, seq)
        except Exception as e:
            log(f"beep generation skipped ({e})")
            self.enabled = False

    def play(self, key):
        if not self.enabled:
            return
        path = os.path.join(self.dir, self.NAMES.get(key, ""))
        if not os.path.exists(path):
            return
        try:
            subprocess.Popen(
                ["aplay", "-q", "-D", self.aplay_device, path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass


def normalize(text):
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def match_command(text, commands):
    tokens = text.split()
    for entry in commands:
        for phrase in entry.get("phrases", []):
            phrase_tokens = normalize(phrase).split()
            if not phrase_tokens:
                continue
            it = iter(tokens)
            if all(tok in it for tok in phrase_tokens):
                return entry
    return None


class RobotApi:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def send_cmd(self, cmd):
        r = requests.post(self.base + "/api/cmd", json=cmd, timeout=2)
        return r.ok

    def send_cv(self, payload):
        r = requests.post(self.base + "/api/cv", json=payload, timeout=2)
        return r.ok

    def send_vlm(self, goal):
        r = requests.post(self.base + "/api/vlm", json={"goal": goal}, timeout=5)
        return r.ok

    def send_vlm_stop(self):
        r = requests.post(self.base + "/api/vlm/stop", json={}, timeout=5)
        return r.ok


class VoiceAssistant:
    def __init__(self, cfg):
        self.cfg = cfg
        self.audio_q = queue.Queue()
        self.api = RobotApi(cfg.get("api_base", "http://127.0.0.1:5000"))
        self.beeps = Beeps(cfg.get("speaker", {}).get("feedback", False),
                           cfg.get("speaker", {}).get("aplay_device", "plughw:0,0"))
        self.commands = cfg.get("commands", [])
        self.move_timeout_s = float(cfg.get("move_timeout_s", 3.0))
        self.vad = cfg.get("vad", {})
        self.stop_deadline = None
        self.model = None

    def load_model(self):
        from useful_moonshine_onnx import MoonshineModel
        name = self.cfg.get("asr", {}).get("model", "tiny")
        log(f"loading moonshine model '{name}' (one-time)...")
        self.model = MoonshineModel(model_name=name)
        log("model loaded")

    def on_audio(self, indata, frames, time_info, status):
        if status:
            log(f"audio status: {status}")
        self.audio_q.put(indata[:, 0].copy())

    def execute(self, entry):
        if "vlm" in entry:
            ok = self.api.send_vlm(entry["vlm"].get("goal", ""))
            log(f"vlm goal -> ok={ok}")
        elif "vlm_stop" in entry:
            ok = self.api.send_vlm_stop()
            log(f"vlm stop -> ok={ok}")
        elif "cv" in entry:
            ok = self.api.send_cv(entry["cv"])
            log(f"cv mode -> {entry['cv']} ok={ok}")
        else:
            ok = self.api.send_cmd(entry["cmd"])
            log(f"cmd -> {entry['cmd']} ok={ok}")
        cmd = entry.get("cmd") or {}
        is_movement = cmd.get("T") == 111 and (cmd.get("FB") or cmd.get("LR"))
        if is_movement:
            self.stop_deadline = time.time() + self.move_timeout_s
        else:
            self.stop_deadline = None
        self.beeps.play("ok" if ok else "err")

    def transcribe(self, audio_i16):
        audio = audio_i16.astype(np.float32) / 32768.0
        texts = self.model.transcribe([audio])
        text = texts[0] if isinstance(texts, list) else texts
        return normalize(str(text))

    def run(self):
        mic = self.cfg.get("mic", {})
        rate = int(mic.get("sample_rate", 16000))
        block = int(rate * BLOCK_MS / 1000)
        dev = find_input_device(mic.get("name_hint", "C-Media"),
                                int(mic.get("fallback_index", 1)))
        if dev is None:
            log("FATAL: no microphone found — check voice_config.yaml mic section")
            sys.exit(0)

        self.load_model()
        thr = float(self.vad.get("rms_threshold", 0.02))
        min_speech = int(self.vad.get("min_speech_ms", 300)) // BLOCK_MS
        silence_blocks = int(self.vad.get("silence_ms", 800)) // BLOCK_MS
        max_blocks = int(self.vad.get("max_utterance_s", 6) * 1000) // BLOCK_MS
        cooldown = int(self.vad.get("cooldown_ms", 300)) // BLOCK_MS

        speech = []
        pre_roll = []
        speech_run = 0
        quiet_run = 0
        total_run = 0
        in_speech = False
        cooldown_left = 0

        log(f"listening on device {dev} ({sd.query_devices(dev)['name']})")
        with sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                            blocksize=block, device=dev, callback=self.on_audio):
            while True:
                try:
                    chunk = self.audio_q.get(timeout=0.1)
                except queue.Empty:
                    chunk = None

                if self.stop_deadline is not None and time.time() >= self.stop_deadline:
                    log("move timeout -> stop")
                    self.api.send_cmd({"T": 111, "FB": 0, "LR": 0})
                    self.stop_deadline = None

                if chunk is None:
                    continue

                rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768.0) ** 2)))
                loud = rms >= thr

                if cooldown_left > 0:
                    cooldown_left -= 1
                    continue

                if not in_speech:
                    pre_roll.append(chunk)
                    if len(pre_roll) > max(min_speech, 1):
                        pre_roll.pop(0)
                    speech_run = speech_run + 1 if loud else 0
                    if speech_run >= min_speech:
                        in_speech = True
                        speech = list(pre_roll)
                        pre_roll = []
                        speech_run = 0
                        quiet_run = 0
                        total_run = 0
                        self.beeps.play("ack")
                else:
                    speech.append(chunk)
                    total_run += 1
                    quiet_run = quiet_run + 1 if not loud else 0
                    if quiet_run >= silence_blocks or total_run >= max_blocks:
                        utterance = np.concatenate(speech)
                        in_speech = False
                        cooldown_left = cooldown
                        text = self.transcribe(utterance)
                        if not text:
                            log("(empty transcript)")
                            continue
                        entry = match_command(text, self.commands)
                        log(f"heard: '{text}'" + (f"  -> {entry['phrases'][0]}" if entry else "  (no match)"))
                        if entry:
                            self.execute(entry)
                        else:
                            self.beeps.play("err")


if __name__ == "__main__":
    try:
        assistant = VoiceAssistant(CFG)
        assistant.run()
    except KeyboardInterrupt:
        log("bye")
    except Exception as e:
        log(f"FATAL: {e}")
        raise
