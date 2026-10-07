"""Tools — schema definitions and the tool runner.

Purpose:
    Defines the set_state tool schema (the only motion path) and the
    ToolRunner that dispatches tool calls to implementations. set_state
    goes through validator.execute(); all other tools are non-motion.

Dependencies:
    json, re; validator for set_state dispatch.

Interface:
    TOOL_SCHEMAS — list of OpenAI-format tool definitions
    ToolRunner(validator) — constructor
    .run(name, arguments) -> dict — dispatch one tool call

Expected outcome:
    set_state is routed to validator.execute() with source="model".
    Unknown tools raise ToolError. Motion never bypasses the validator.
"""

import json
import re

HEX_RX = re.compile(r"^[0-9A-F]{6}$")


class ToolError(Exception):
    pass


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "set_state",
            "description": "Set the robot's motion state via a 6-char hex code. "
                           "This is the ONLY way to move the robot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "pattern": "^[0-9A-F]{6}$",
                        "description": "6 uppercase hex chars (e.g. 060300 = turn left)"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "Store a fact or episode summary in memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"}
                },
                "required": ["key", "value"]
            }
        }
    }
]


class ToolRunner:
    def __init__(self, validator, memory=None):
        self.validator = validator
        self.memory = memory or {}

    def run(self, name, arguments):
        if name == "set_state":
            code = arguments.get("code", "")
            if not isinstance(code, str) or not HEX_RX.match(code):
                raise ToolError(f"set_state: invalid hex code {code!r}")
            return self.validator.execute(code, source="model")

        if name == "memory_write":
            key = str(arguments.get("key", ""))[:200]
            value = str(arguments.get("value", ""))[:2000]
            if len(self.memory) >= 200:
                oldest = next(iter(self.memory))
                del self.memory[oldest]
            self.memory[key] = value
            return {"stored": key}

        raise ToolError(f"unknown tool: {name}")

    def extract_tool_call(self, model_output):
        """Parse a model response for tool calls (JSON or tagged format)."""
        try:
            data = json.loads(model_output)
            if isinstance(data, dict) and "tool_calls" in data:
                calls = data["tool_calls"]
                if calls and isinstance(calls, list):
                    fn = calls[0].get("function", {})
                    return fn.get("name"), fn.get("arguments", {})
            if isinstance(data, dict) and "name" in data:
                return data["name"], data.get("arguments", data.get("parameters", {}))
        except (json.JSONDecodeError, TypeError):
            pass

        import re
        m = re.search(
            r'(?:set_state|tool_call)\s*\(\s*["\']?([0-9A-F]{6})["\']?\s*\)',
            model_output)
        if m:
            return "set_state", {"code": m.group(1)}

        m = re.search(r'\b([0-9A-F]{6})\b', model_output)
        if m and "hex" in model_output.lower():
            return "set_state", {"code": m.group(1)}

        return None, {}
