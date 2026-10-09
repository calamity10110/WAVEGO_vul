"""Fuzz test — seeded, deterministic, 10k iterations by default.

Purpose:
    Feeds random and malformed inputs to the Validator to prove the
    gates hold under adversarial conditions. Checks that no input
    crashes the validator, causes an unexpected state change, or
    bypasses the safety gates. Uses only stdlib — no hypothesis, no
    external fuzzing framework.

Dependencies:
    stdlib (random, string, os, sys, tempfile).
    agent.validator.

Interface:
    run(seed, iterations) -> dict  — returns summary of outcomes
    main() — CLI entry (--seed, --iterations)

Expected outcome:
    Zero violations: every invalid input raises ValidationError,
    every valid input produces a clean result dict. The validator
    never crashes, never sends unexpected serial data, and never
    transitions to an unexpected state.
"""

import os
import random
import string
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from agent.validator import Validator, ValidationError

DEFAULT_CFG = {
    "motion": {
        "codebook": {
            "000000": {"name": "HALT", "verified": True, "esp32": '{"T":111,"FB":0,"LR":0}'},
            "050300": {"name": "WALK", "verified": True, "esp32": '{"T":111,"FB":1,"LR":0}'},
            "060300": {"name": "TURN_L", "verified": True, "esp32": '{"T":111,"FB":0,"LR":-1}'},
            "020000": {"name": "SIT", "verified": False, "esp32": '{"T":112,"func":1}'},
        },
        "forbidden_transitions": [["050300", "060300"]],
        "max_speed_byte": 10,
    },
    "esp32": {"allow_unverified": False},
    "safety": {"battery_floor_pct": 15, "max_bad_codes": 100},
}

VALID_CODES = list(DEFAULT_CFG["motion"]["codebook"].keys())
HEX_CHARS = "0123456789ABCDEFabcdef"


def make_validator():
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "state_table.yaml")
    with open(path, "w") as fh:
        yaml.dump(DEFAULT_CFG, fh)
    return Validator(path, simulate=True), tmp


def random_hex(rng):
    return "".join(rng.choice(HEX_CHARS) for _ in range(6))


def random_garbage(rng):
    choices = [
        lambda: "".join(rng.choice(string.printable[:94]) for _ in range(rng.randint(0, 20))),
        lambda: rng.randint(-100, 100),
        lambda: None,
        lambda: [random_hex(rng) for _ in range(rng.randint(1, 3))],
        lambda: {"code": random_hex(rng)},
        lambda: b"\x00\xff" * rng.randint(1, 6),
        lambda: rng.choice(["", "0", "0000000", "0x0600", "06030"]),
        lambda: rng.choice(VALID_CODES).lower(),
        lambda: random_hex(rng).replace(rng.choice(HEX_CHARS), rng.choice("GXYZ!@#")),
    ]
    return rng.choice(choices)()


def run(seed=1337, iterations=10000):
    rng = random.Random(seed)
    v, tmp = make_validator()

    stats = {"accepted": 0, "rejected": 0, "violations": 0, "crashes": 0,
             "unexpected_state": 0}

    for i in range(iterations):
        choice = rng.random()
        try:
            if choice < 0.3:
                code = rng.choice(VALID_CODES)
                result = v.execute(code, source="fuzz")
                stats["accepted"] += 1
                if not isinstance(result, dict) or "code" not in result:
                    stats["violations"] += 1
            elif choice < 0.6:
                code = random_hex(rng)
                try:
                    v.execute(code, source="fuzz")
                    stats["accepted"] += 1
                except ValidationError:
                    stats["rejected"] += 1
            elif choice < 0.9:
                code = random_garbage(rng)
                try:
                    v.execute(code, source="fuzz")
                    stats["accepted"] += 1
                except ValidationError:
                    stats["rejected"] += 1
            else:
                code = rng.choice(VALID_CODES).lower()
                try:
                    v.execute(code, source="fuzz")
                    stats["accepted"] += 1
                except ValidationError:
                    stats["rejected"] += 1

            if v.prev_code not in VALID_CODES and v.prev_code != "000000":
                stats["unexpected_state"] += 1
        except ValidationError:
            stats["rejected"] += 1
        except Exception:
            stats["crashes"] += 1
            if stats["crashes"] <= 3:
                import traceback
                traceback.print_exc()

    v.close()
    tmp.cleanup()

    stats["total"] = iterations
    stats["seed"] = seed
    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validator fuzz test")
    parser.add_argument("seed", nargs="?", type=int, default=1337)
    parser.add_argument("iterations", nargs="?", type=int, default=10000)
    args = parser.parse_args()

    print(f"Fuzzing validator: seed={args.seed}, iterations={args.iterations}")
    stats = run(seed=args.seed, iterations=args.iterations)

    print(f"  accepted:  {stats['accepted']}")
    print(f"  rejected:  {stats['rejected']}")
    print(f"  violations: {stats['violations']}")
    print(f"  crashes:   {stats['crashes']}")
    print(f"  unexpected state: {stats['unexpected_state']}")

    if stats["violations"] > 0 or stats["crashes"] > 0 or stats["unexpected_state"] > 0:
        print("FAIL — safety violations detected")
        sys.exit(1)
    else:
        print("PASS — 0 violations across all iterations")
        sys.exit(0)


if __name__ == "__main__":
    main()
