"""Text-vs-label coherence heuristic.

Catches narrative/command drift for movement labels (reason says one
direction, label another). Stance labels (stop/none/sit/stand/jump) never
hard-fail: movement words in their reasons are usually the justification
("person walking ahead" + stop is coherent). A direction word preceded by a
blocker ("cannot", "obstacle", ...) is not a conflict. Returns (ok, note);
ok=False feeds the retry ladder.
"""

import re

_FAMILIES = {
    "forward": re.compile(r"\b(forward|ahead|straight|advance|walking)\b", re.I),
    "back": re.compile(r"\b(back|backward|reverse|retreat)\b", re.I),
    "turn_left": re.compile(r"\b(left|counterclockwise)\b", re.I),
    "turn_right": re.compile(r"\b(right|clockwise)\b", re.I),
}
_BLOCKER = re.compile(
    r"\b(cannot|can't|unable|blocked|obstacle|obstructed|stopped|halted|no|not)\b", re.I)
_WINDOW = 30


def coherent(label, reason):
    if label not in _FAMILIES:
        return True, "stance"
    conflicts = []
    for fam, rx in _FAMILIES.items():
        if fam == label:
            continue
        for m in rx.finditer(reason):
            prefix = reason[max(0, m.start() - _WINDOW):m.start()]
            suffix = reason[m.end():m.end() + _WINDOW]
            if not (_BLOCKER.search(prefix) or _BLOCKER.search(suffix)):
                conflicts.append(fam)
                break
    if conflicts:
        return False, f"reason mentions {conflicts[0]} but label is {label}"
    if _FAMILIES[label].search(reason):
        return True, "matched"
    return True, "generic"
