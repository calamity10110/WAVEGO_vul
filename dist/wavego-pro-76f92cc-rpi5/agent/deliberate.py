"""Deliberate — L2 arbitration and the single VLM tool call.

Purpose:
    Fuses perception + mission + user input, applies the fixed
    arbitration order (Safety > user > mission > idle), then makes
    one VLM call with tool schemas. Returns the model's decision.

Dependencies:
    model.VLModel, tools.TOOL_SCHEMAS.
"""

from .model import ModelError


ARBITRATION_ORDER = ["safety", "user", "mission", "idle"]


class Deliberate:
    def __init__(self, model, tool_schemas):
        self.model = model
        self.tools = tool_schemas

    def decide(self, perception, mission=None):
        branch = self._arbitrate(perception, mission)

        if branch == "safety":
            return {"action": "halt", "branch": branch,
                    "reason": perception.get("safety_reason", "safety gate")}

        context = self._build_context(perception, mission, branch)
        try:
            raw = self.model.decide(context, self.tools)
            return {"action": "model", "branch": branch, "raw": raw}
        except ModelError:
            return {"action": "halt", "branch": branch,
                    "reason": "model unreachable — conservative halt"}

    def _arbitrate(self, perception, mission):
        if perception.get("safety_flag"):
            return "safety"
        if perception.get("intent") and perception["intent"].get("intent") != "unknown":
            return "user"
        if mission and mission.get("active"):
            return "mission"
        return "idle"

    def _build_context(self, perception, mission, branch):
        parts = [f"Branch: {branch}"]
        if perception.get("summary"):
            parts.append(f"Vision: {perception['summary']}")
        if perception.get("intent") and perception["intent"].get("intent") != "unknown":
            parts.append(f"Intent: {perception['intent'].get('intent')}")
        if mission and mission.get("description"):
            parts.append(f"Mission: {mission['description']}")
        parts.append("Available hex codes: HALT=000000 STAND=010000 "
                     "WALK=050300 TURN_L=060300 TURN_R=070300 BACK=080300")
        return "\n".join(parts)
