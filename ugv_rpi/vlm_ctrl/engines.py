"""Engine wrappers: onboard Intern-Decision (fast) + optional online verifier.

FastEngine wraps the model's DecisionEngine (lazy import — torch must never
load in tests or in the main Flask app, only in this service process). The
predict function is injectable for testing. Output is mapped into the
decision vocabulary of contracts.py; the free-form "reason" is composed
from the chosen fields (template text — the fast engine cannot hallucinate
narrative because it does not generate text).
"""

import json
import sys

LABEL_HELP = {
    "forward": "walk forward (T:111 FB=1)",
    "back": "walk backward (T:111 FB=-1)",
    "turn_left": "turn left in place (T:111 LR=-1)",
    "turn_right": "turn right in place (T:111 LR=1)",
    "stop": "stop and stand (T:111 FB=0 LR=0)",
    "sit": "stay low (T:112 func=1)",
    "stand": "stand up at default height (T:110)",
    "jump": "perform jump demo (T:112 func=3)",
    "none": "no movement this cycle (observe)",
}

STATUS_HELP = {
    "in_progress": "goal not yet achieved, keep working",
    "reached": "goal is visibly achieved in the frames",
    "impossible": "goal cannot be achieved from what is visible",
}


def build_fast_questions(goal):
    return {
        "state": (
            f"Robot goal: {goal}. Decide the next action from the robot camera view. "
            "Movement commands map to quadruped gait vectors; stance commands are pose changes."
        ),
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "Which action should the robot take this cycle?",
                "criteria": dict(LABEL_HELP),
            },
            "goal_status": {
                "type": "choice",
                "instructions": "Is the goal achieved?",
                "criteria": dict(STATUS_HELP),
            },
        },
    }


def conf_to_enum(p):
    if p >= 0.75:
        return "high"
    if p >= 0.55:
        return "medium"
    return "low"


class FastEngine:
    def __init__(self, checkpoint_dir, media_root, device="cpu", predict_fn=None):
        self.checkpoint_dir = checkpoint_dir
        self.media_root = media_root
        self.device = device
        self._predict_fn = predict_fn
        self._engine = None

    def load(self):
        if self._predict_fn is not None:
            return
        if self.checkpoint_dir not in sys.path:
            sys.path.insert(0, self.checkpoint_dir)
        from inference import DecisionEngine
        self._engine = DecisionEngine(checkpoint=self.checkpoint_dir,
                                      media_root=self.media_root, device=self.device)

    def predict(self, request):
        if self._predict_fn is not None:
            return self._predict_fn(request)
        return self._engine.predict(request)

    def decide(self, goal, image_path, feedback=None):
        request = build_fast_questions(goal)
        if feedback:
            request["state"] += " Previous attempt rejected: " + str(feedback)
        request["images"] = [image_path]
        resp = self.predict(request)
        answers = resp["answers"]
        label = answers["action"]["decision"]
        status = answers["goal_status"]["decision"]
        conf = float(answers["action"].get("confidence", 0.0))
        status_conf = float(answers["goal_status"].get("confidence", 0.0))
        reason = "{} (action p={:.2f}, status p={:.2f})".format(label, conf, status_conf)
        return {
            "label": label,
            "goal_status": status,
            "reason": reason,
            "confidence": conf_to_enum(conf),
            "_id_conf": conf,
        }


class OnlineVerifier:
    """Optional cloud/LAN verifier behind an OpenAI-compatible chat API.

    verify() returns (verdict, method, note); verdict True/False/None with
    None meaning unavailable or unclear — the caller falls back to software
    checks. Never raises.
    """

    def __init__(self, base_url, model, api_key="", timeout_s=10.0,
                 send_images=False, poster=None):
        self.enabled = bool(base_url)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.send_images = send_images
        self._poster = poster or self._post

    def _post(self, payload):
        import requests
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return requests.post(self.base_url + "/chat/completions",
                             json=payload, headers=headers, timeout=self.timeout_s)

    def verify(self, goal, decision, image_b64=None):
        if not self.enabled:
            return None, "online", "disabled"
        content = [
            {"type": "text", "text": (
                f'Robot goal: "{goal}". The onboard decision model chose: '
                f'{json.dumps(decision)}. Is this choice consistent with the '
                'goal and the camera view? Reply with JSON only: '
                '{"verdict": "agree"|"disagree"|"unclear", "note": "<= 12 words"}')}
        ]
        if self.send_images and image_b64:
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + image_b64}})
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": 40,
        }
        try:
            r = self._poster(payload)
            text = r.json()["choices"][0]["message"]["content"]
        except Exception:
            return None, "online", "unreachable"
        try:
            body = json.loads(text[text.index("{"):text.rindex("}") + 1])
            v = body.get("verdict")
            if v == "agree":
                return True, "online", body.get("note", "")
            if v == "disagree":
                return False, "online", body.get("note", "")
        except Exception:
            pass
        return None, "online", "unclear response"
