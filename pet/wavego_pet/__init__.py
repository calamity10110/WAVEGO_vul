"""wavego_pet — a robot pet for WAVEGO Pro, powered by Qwen3-VL-2B on the Pi.

Scenes from the VLM drive a deterministic persona; the persona emits
semantic commands into the wavego_agent motion supervisor. See pet/README.md
for the fresh-RPi5-OS installation guide.
"""

from .brain import PetBrain
from .persona import PersonaConfig, PetPersona, PetState, Scene
from .vlm_client import FakeVLM, VLMClient, extract_json, scene_from_reply
from .vision import CameraFrames, StaticFrames, TINY_JPEG

__all__ = [
    "CameraFrames",
    "FakeVLM",
    "PetBrain",
    "PersonaConfig",
    "PetPersona",
    "PetState",
    "Scene",
    "StaticFrames",
    "TINY_JPEG",
    "VLMClient",
    "extract_json",
    "scene_from_reply",
]
