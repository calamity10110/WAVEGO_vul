"""Command contracts for the VLM control path.

The model never emits raw T-commands. It selects a semantic label; the
library compiles the wire JSON from LABEL_COMMANDS below. This keeps the
dispatch surface deny-by-default: only whitelisted commands derived from
validated labels can ever reach /api/cmd.
"""

import jsonschema

LABEL_COMMANDS = {
    "forward":    {"T": 111, "FB": 1,  "LR": 0},
    "back":       {"T": 111, "FB": -1, "LR": 0},
    "turn_left":  {"T": 111, "FB": 0,  "LR": -1},
    "turn_right": {"T": 111, "FB": 0,  "LR": 1},
    "stop":       {"T": 111, "FB": 0,  "LR": 0},
    "sit":        {"T": 112, "func": 1},
    "stand":      {"T": 110},
    "jump":       {"T": 112, "func": 3},
    "none":       None,
}

MOVEMENT_LABELS = ("forward", "back", "turn_left", "turn_right")
GOAL_STATUSES = ("in_progress", "reached", "impossible")
WHITELIST_TS = frozenset(c["T"] for c in LABEL_COMMANDS.values() if c)

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["label", "goal_status", "reason"],
    "properties": {
        "label": {"enum": sorted(LABEL_COMMANDS)},
        "goal_status": {"enum": list(GOAL_STATUSES)},
        "reason": {"type": "string", "minLength": 3, "maxLength": 160},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}

_validator = jsonschema.Draft202012Validator(DECISION_SCHEMA)


def derive_command(label):
    if label not in LABEL_COMMANDS:
        raise ValueError(f"unknown label: {label}")
    cmd = LABEL_COMMANDS[label]
    return dict(cmd) if cmd is not None else None


def validate_decision(obj):
    """Return (clean_decision, errors). errors empty == accepted.

    Underscore-prefixed keys (_id_conf, _frame_hash, ...) are private
    engine metadata: they bypass the schema and are re-attached to the
    clean decision for downstream consumers (verifier, session log).
    """
    if not isinstance(obj, dict):
        return None, ["decision must be a JSON object"]
    private = {k: v for k, v in obj.items() if k.startswith("_")}
    public = {k: v for k, v in obj.items() if not k.startswith("_")}
    errors = [f"{e.path}: {e.message}" for e in _validator.iter_errors(public)]
    if errors:
        return None, errors
    clean = {
        "label": public["label"],
        "goal_status": public["goal_status"],
        "reason": public["reason"].strip(),
        "confidence": public.get("confidence", "medium"),
    }
    clean.update(private)
    return clean, []
