"""Execute — L3 tool-call dispatch.

Purpose:
    Takes the deliberation decision and dispatches tool calls via
    ToolRunner. Never contains motion logic itself — all motion
    goes through set_state → validator.execute().
"""

import json

from .tools import ToolRunner, ToolError


class Execute:
    def __init__(self, tool_runner: ToolRunner):
        self.runner = tool_runner

    def dispatch(self, decision):
        if decision.get("action") == "halt":
            self.runner.validator.halt(source="deliberate")
            return [{"name": "HALT", "result": "halted"}]

        raw = decision.get("raw", "")
        name, args = self.runner.extract_tool_call(raw)
        if name is None:
            return []

        try:
            result = self.runner.run(name, args)
            return [{"name": name, "result": result}]
        except ToolError as e:
            return [{"name": name, "error": str(e)}]
        except Exception as e:
            self.runner.validator.halt(source="execute_error")
            return [{"name": name, "error": f"execution error: {e}"}]
