"""Whitelisted runtime settings for the /api/config surface.

Flat dotted keys map to nested config.yaml paths. validate_patch() rejects
anything outside the whitelist or out of range; apply_patch() mutates the
loaded config dict in place so the caller can persist it with yaml.dump.
Pure module — no Flask imports, unit-testable off-robot.
"""

_INT = "int"
_FLOAT = "float"
_STR = "str"

SPEC = {
    "video.default_res_w":    (_INT,   320, 1920),
    "video.default_res_h":    (_INT,   240, 1080),
    "video.default_quality":  (_INT,     1,  100),
    "args_config.max_speed":  (_FLOAT, 0.1,  2.0),
    "args_config.slow_speed": (_FLOAT, 0.0,  1.0),
    "args_config.min_rate":   (_FLOAT, 0.0,  1.0),
    "args_config.mid_rate":   (_FLOAT, 0.0,  1.0),
    "args_config.max_rate":   (_FLOAT, 0.0,  1.0),
    "base_config.robot_name": (_STR,     1,   32),
}
def get_settings(config):
    out = {}
    for key in SPEC:
        section, field = key.split(".")
        out[key] = config.get(section, {}).get(field)
    return out


def validate_patch(patch):
    """Return (clean, errors): clean maps dotted key -> coerced value."""
    if not isinstance(patch, dict):
        return {}, ["patch must be a JSON object"]
    clean, errors = {}, []
    for key, value in patch.items():
        if key not in SPEC:
            errors.append(f"{key}: unknown setting")
            continue
        kind, lo, hi = SPEC[key]
        try:
            if kind is _INT:
                v = int(value)
                if not lo <= v <= hi:
                    raise ValueError
            elif kind is _FLOAT:
                v = round(float(value), 3)
                if not lo <= v <= hi:
                    raise ValueError
            else:
                v = str(value).strip()
                if not lo <= len(v) <= hi:
                    raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{key}: must be {kind} in [{lo}, {hi}]")
            continue
        clean[key] = v
    return clean, errors


def apply_patch(config, clean):
    for key, value in clean.items():
        section, field = key.split(".")
        config.setdefault(section, {})[field] = value
    return config
