"""Perceive — L1 vision and intent processing.

Purpose:
    Runs vision.describe and intent.parse on the ingested inputs.
    Degrades gracefully when the model is unreachable or the camera
    is absent. Produces a context dict for the deliberation layer.
"""

from .model import ModelError


class Perceive:
    def __init__(self, model):
        self.model = model

    def process(self, frame, user_input=None):
        result = {"summary": "", "intent": None, "vision_ok": False, "intent_ok": False}

        if frame is not None:
            try:
                import base64
                import cv2
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                img_b64 = base64.b64encode(buf).decode("utf-8")
                result["summary"] = self.model.describe(img_b64)
                result["vision_ok"] = True
            except (ModelError, Exception):
                result["summary"] = "vision unavailable"
        else:
            result["summary"] = "no camera frame"

        if user_input and user_input.get("text"):
            try:
                result["intent"] = self.model.parse_intent(user_input["text"])
                result["intent_ok"] = True
            except (ModelError, Exception):
                result["intent"] = {"intent": "unknown", "priority": 1}

        return result
