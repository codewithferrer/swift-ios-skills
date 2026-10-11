"""Where a planned event would go.

For an event that is in the plan but not in the code, rank the places in the
app whose names say the same thing: functions, controls, views and App
Intents. This is name matching with a small vocabulary of equivalent verbs, so
the answer is a short list of candidates to open, not a decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import analytics_coverage as ac
import analytics_model as am
import swift_source as sw

VERBS = {
    "created": {"create", "created", "add", "added", "new", "save", "saved", "insert", "make", "schedule",
                "scheduled", "register", "registered"},
    "completed": {"complete", "completed", "success", "succeeded", "solved", "solve", "finish", "finished", "done",
                  "pass", "passed", "win", "won", "dismiss", "dismissed", "stop", "stopped", "turnoff"},
    "failed": {"fail", "failed", "failure", "error", "wrong", "invalid", "incorrect", "lose", "lost", "cancelled",
               "canceled"},
    "shown": {"show", "shown", "present", "presented", "appear", "appeared", "display", "displayed", "view",
              "viewed"},
    "fired": {"fire", "fired", "ring", "rings", "ringing", "rang", "trigger", "triggered", "alert", "alerting"},
    "used": {"use", "used", "exit", "tap", "tapped", "press", "pressed"},
    "updated": {"update", "updated", "change", "changed", "increment", "bump", "refresh", "advance", "record",
                "recorded"},
    "invoked": {"perform", "invoke", "invoked", "run", "execute", "launch", "call", "called"},
    "opened": {"open", "opened"},
    "started": {"start", "started", "begin", "began"},
    "selected": {"select", "selected", "choose", "chosen", "pick", "picked"},
    "deleted": {"delete", "deleted", "remove", "removed", "cancel"},
    "completed_purchase": set(),
}
VERB_WORDS = {w for ws in VERBS.values() for w in ws} | set(VERBS)


def tokens(text: str) -> list:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    words = [w.lower() for w in re.split(r"[^A-Za-z0-9]+", text) if w]
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]


def _canon(word: str) -> set:
    out = {word}
    for canon, syn in VERBS.items():
        if word == canon or word in syn:
            out.add(canon)
            out |= syn
    return out


@dataclass
class Candidate:
    score: float
    kind: str
    name: str
    file: Path
    line: int
    signature: str
    matched: list = field(default_factory=list)


def candidates_for(model: am.Model, cov: ac.Coverage, event: str, limit: int = 3) -> list:
    words = tokens(event)
    nouns = [w for w in words if w not in VERB_WORDS and w not in ("app",)]
    verbs = [w for w in words if w in VERB_WORDS]
    if not nouns:
        nouns = words
    facade_names = {(m.owner, m.name) for m in model.methods()}
    out: list = []

    def score(parts: list, weight_owner: float = 0.5):
        """parts: [(token list, weight)] -> (score, matched)"""
        matched = []
        total = 0.0
        for toks, weight in parts:
            toks = set(toks)
            for n in nouns:
                if n in toks:
                    total += 3 * weight
                    matched.append(n)
            for v in verbs:
                if toks & _canon(v):
                    total += 2 * weight
                    matched.append(v)
        return total, sorted(set(matched))

    wants_view = any(v in _canon("shown") for v in verbs)
    wants_intent = any(v in _canon("invoked") for v in verbs)

    def finish(sc: float, matched: list, kind: str) -> float:
        """Without the verb the name only says the topic: demote. Prefer the kind of place the verb implies."""
        if verbs and not any(v in matched for v in verbs):
            sc *= 0.35
        if kind == "view" and wants_view or kind == "intent" and wants_intent or \
                kind == "function" and not (wants_view or wants_intent):
            sc *= 1.3
        return sc

    for sf in model.files:
        parts_lower = {p.lower() for p in sf.path.parts}
        if any(p.endswith("tests") or p.endswith("uitests") or "debug" in p or p == "previews" for p in parts_lower):
            continue
        for fn in sf.funcs:
            if fn.name in ("body", "init") or not fn.body or (fn.owner, fn.name) in facade_names:
                continue
            if fn.owner in model.facades:
                continue
            sc, matched = score([(tokens(fn.name), 1.0), (tokens(fn.owner or ""), 0.5), (tokens(sf.path.stem), 0.4)])
            sc = finish(sc, matched, "function")
            if sc >= 3 and any(n in matched for n in nouns):
                flat = re.sub(r"\s+", " ", fn.params)[:80]
                prefix = fn.owner + "." if fn.owner else ""
                sig = f"{prefix}{fn.name}({flat})"
                out.append(Candidate(sc, "function", fn.name, sf.path, sf.line(fn.start), sig, matched))
        for decl in sf.types:
            if decl.keyword in ("struct", "class") and re.search(r"\b(View|\w*Intent)\b", decl.inherits):
                is_intent = "Intent" in decl.inherits
                sc, matched = score([(tokens(decl.name), 1.0)])
                if (is_intent and "invoked" in {c for v in verbs for c in _canon(v)} | set(verbs)) or \
                        ("shown" in verbs or any(v in _canon("shown") for v in verbs)):
                    sc += 1 if sc else 0
                sc = finish(sc, matched, "intent" if is_intent else "view")
                if sc >= 3 and any(n in matched for n in nouns):
                    out.append(Candidate(sc + 0.5, "intent" if is_intent else "view", decl.name, sf.path,
                                         sf.line(decl.start), decl.name, matched))
    for control in cov.controls:
        sc, matched = score([(tokens(control.label or ""), 1.0), (tokens(" ".join(control.callees)), 0.8)])
        sc = finish(sc, matched, "control")
        if sc >= 3 and any(n in matched for n in nouns):
            out.append(Candidate(sc, f"{control.kind.lower()} control", control.label or "(no label)", control.file,
                                 control.line, f"{control.kind} in {control.view}", matched))
    out.sort(key=lambda c: (-c.score, c.file.as_posix(), c.line))
    seen, picked = set(), []
    for c in out:
        key = (c.file, c.line)
        if key in seen:
            continue
        seen.add(key)
        picked.append(c)
        if len(picked) == limit:
            break
    return picked
