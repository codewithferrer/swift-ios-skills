#!/usr/bin/env python3
"""Maintenance tool for this repository of agent skills.

    python3 scripts/skills_repo.py validate   # check every skill, the marketplace and the README
    python3 scripts/skills_repo.py sync       # regenerate .claude-plugin/marketplace.json

`sync` derives the Claude Code marketplace from skills/*/SKILL.md, so adding
a skill is: create skills/<name>/SKILL.md, run `sync`, add a README row.
`validate` runs in CI and fails when any of that was forgotten.

Standard library only. Frontmatter is parsed with a deliberately small
reader that supports single-line `key: value` pairs, which is all a skill
needs; anything fancier is reported as an error rather than misread.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
README = ROOT / "README.md"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED_WORDS = ("anthropic", "claude")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
RECOMMENDED_MAX_LINES = 500
BUNDLE_NAME = "all-skills"

DEFAULT_MARKETPLACE = {
    "name": "codewithferrer-skills",
    "owner": {"name": "Daniel Ferrer"},
    "metadata": {"description": "Agent skills for iOS and Swift development", "version": "0.1.0"},
}


class FrontmatterError(ValueError):
    pass


def parse_frontmatter(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("must start with a '---' frontmatter block")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise FrontmatterError("frontmatter block is never closed with '---'") from None

    values = {}
    for number, line in enumerate(lines[1:end], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)$", line)
        if not match:
            raise FrontmatterError(
                f"line {number}: only single-line 'key: value' pairs are supported "
                "(no multi-line or folded values)"
            )
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value[:1] in ("|", ">"):
            raise FrontmatterError(f"line {number}: '{key}' uses a multi-line value; put it on one line")
        values[key] = value
    return values


def skill_dirs() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))


def load_skills() -> tuple[dict, list[str]]:
    """name -> frontmatter, plus the structural errors found on the way."""
    skills, errors = {}, []
    for directory in skill_dirs():
        skill_file = directory / "SKILL.md"
        label = f"skills/{directory.name}"
        if not skill_file.is_file():
            errors.append(f"{label}: missing SKILL.md")
            continue
        try:
            meta = parse_frontmatter(skill_file)
        except FrontmatterError as exc:
            errors.append(f"{label}/SKILL.md: {exc}")
            continue
        skills[directory.name] = meta
    return skills, errors


# -- validate ----------------------------------------------------------------


def check_skill(dirname: str, meta: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    label = f"skills/{dirname}/SKILL.md"

    name = meta.get("name", "")
    if not name:
        errors.append(f"{label}: 'name' is required")
    else:
        if name != dirname:
            errors.append(f"{label}: name '{name}' must match the directory name '{dirname}'")
        if not NAME_RE.match(name) or len(name) > MAX_NAME:
            errors.append(f"{label}: name must be lowercase letters, digits and hyphens, at most {MAX_NAME} characters")
        if any(word in name for word in RESERVED_WORDS):
            errors.append(f"{label}: name must not contain {' or '.join(RESERVED_WORDS)}")

    description = meta.get("description", "")
    if not description:
        errors.append(f"{label}: 'description' is required")
    else:
        if len(description) > MAX_DESCRIPTION:
            errors.append(f"{label}: description is {len(description)} characters, the limit is {MAX_DESCRIPTION}")
        if re.search(r"[<>]", description):
            errors.append(f"{label}: description must not contain < or > (XML tags are not allowed)")

    text = (SKILLS_DIR / dirname / "SKILL.md").read_text(encoding="utf-8")
    line_count = text.count("\n") + 1
    if line_count > RECOMMENDED_MAX_LINES:
        warnings.append(f"{label}: {line_count} lines, consider moving detail into references/ (recommended <= {RECOMMENDED_MAX_LINES})")

    # Paths mentioned in backticks must exist: catches stale references after a move.
    for relative in sorted(set(re.findall(r"`((?:references|scripts|examples)/[A-Za-z0-9_./-]+)`", text))):
        if not (SKILLS_DIR / dirname / relative).exists():
            errors.append(f"{label}: refers to `{relative}`, which does not exist")
    return errors, warnings


def validate() -> int:
    skills, errors = load_skills()
    warnings: list[str] = []

    if not skills and not errors:
        errors.append("no skills found under skills/")

    for dirname, meta in skills.items():
        skill_errors, skill_warnings = check_skill(dirname, meta)
        errors += skill_errors
        warnings += skill_warnings

    if skills:
        expected = build_marketplace(skills)
        if not MARKETPLACE.is_file():
            errors.append(f"{MARKETPLACE.relative_to(ROOT)} is missing -- run: python3 scripts/skills_repo.py sync")
        elif json.loads(MARKETPLACE.read_text()) != expected:
            errors.append(f"{MARKETPLACE.relative_to(ROOT)} is out of date -- run: python3 scripts/skills_repo.py sync")

        readme = README.read_text(encoding="utf-8") if README.is_file() else ""
        for name in skills:
            if f"`{name}`" not in readme:
                errors.append(f"README.md does not list the skill `{name}` in its catalog")

    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        print(f"\n{len(errors)} error(s).", file=sys.stderr)
        return 1
    print(f"OK: {len(skills)} skill(s), marketplace in sync, README catalog complete.")
    return 0


# -- sync --------------------------------------------------------------------


def build_marketplace(skills: dict) -> dict:
    """Top-level name/owner/metadata are kept from the existing file (they
    are the hand-edited part); the plugins list is always derived.
    """
    base = dict(DEFAULT_MARKETPLACE)
    if MARKETPLACE.is_file():
        existing = json.loads(MARKETPLACE.read_text())
        base.update({k: existing[k] for k in ("name", "owner", "metadata") if k in existing})
    version = base["metadata"].get("version", DEFAULT_MARKETPLACE["metadata"]["version"])

    plugins = []
    for name in sorted(skills):
        plugins.append({
            "name": name,
            "source": "./",
            "description": skills[name].get("description", ""),
            "version": version,
            "category": "development",
            "strict": False,
            "skills": [f"./skills/{name}"],
        })
    plugins.append({
        "name": BUNDLE_NAME,
        "source": "./",
        "description": "Every skill in this repository, installed together.",
        "version": version,
        "category": "development",
        "strict": False,
        "skills": [f"./skills/{name}" for name in sorted(skills)],
    })
    return {**base, "plugins": plugins}


def sync() -> int:
    skills, errors = load_skills()
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    if not skills:
        print("error: no skills found under skills/", file=sys.stderr)
        return 1
    MARKETPLACE.parent.mkdir(parents=True, exist_ok=True)
    MARKETPLACE.write_text(json.dumps(build_marketplace(skills), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {MARKETPLACE.relative_to(ROOT)} with {len(skills)} skill(s) plus the '{BUNDLE_NAME}' bundle.")
    return 0


def main(argv: list[str]) -> int:
    commands = {"validate": validate, "sync": sync}
    if len(argv) != 2 or argv[1] not in commands:
        print(__doc__, file=sys.stderr)
        return 2
    return commands[argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
