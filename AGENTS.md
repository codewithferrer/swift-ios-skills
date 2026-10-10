# Guidance for agents working in this repository

## What this repo is

A collection of agent skills for iOS and Swift development. It is a content and
tooling repository, not an app or a Swift package: the work is writing and
maintaining `skills/<name>/SKILL.md` files and the scripts and references
beside them.

## Layout

- `skills/<name>/SKILL.md` - the skill: YAML frontmatter plus instructions.
- `skills/<name>/references/*.md` - detail loaded only when the task needs it.
- `skills/<name>/scripts/`, `examples/`, `tests/` - code, templates, tests.
- `.claude-plugin/marketplace.json` - **generated**. Never edit by hand.
- `scripts/skills_repo.py` - `validate` and `sync` for the repo.
- `README.md` - public catalog and install instructions.
- `CHANGELOG.md` - what changed in the public surface (new skills, bundle changes).

## Rules for writing a skill

- Write everything in English, including comments, messages and examples.
- `name` equals the directory name: lowercase letters, digits, hyphens, at most
  64 characters, and no `claude` or `anthropic` in it.
- `description` is one line, at most 1024 characters, no `<` or `>`. It is the
  only thing the agent sees before deciding to load the skill, so say what the
  skill does *and* the situations that should trigger it, including phrasings
  where the user never names the tool.
- Frontmatter values stay on a single line; the validator reads nothing else.
- Keep `SKILL.md` short (well under 500 lines). Move detail to `references/`
  and tell the reader when to open each file.
- Skills are self-contained. Mention a sibling skill if useful, but never rely
  on its files.
- Explain *why* a rule exists instead of shouting it; agents follow reasoning
  better than capitals.

## Rules for skills that ship scripts

- No secrets, ids or personal data in the repository. Credentials and per-user
  config live outside the skill folder (for example `~/.config/<skill-name>/`).
- Anything that writes to an external service gets a preview mode, and
  anything irreversible requires an explicit flag plus a typed confirmation.
  "Do nothing" is the default for the irreversible step.
- Dependencies are optional at the edges: `--help` and dry runs should work
  before third-party packages are installed.
- Put offline unit tests in `skills/<name>/tests/` and keep them free of
  network access and credentials.

## Adding or changing a skill

1. Create or edit `skills/<name>/SKILL.md` (and the files beside it).
2. `python3 scripts/skills_repo.py sync` to regenerate the marketplace.
3. Add or update the row in the `README.md` skills table.
4. `python3 scripts/skills_repo.py validate`, and run the skill's tests.
5. Add an entry to `CHANGELOG.md`; bump `metadata.version` in
   `.claude-plugin/marketplace.json` for a release, then run `sync` again so
   every plugin picks up the version.

## Checking a skill's behaviour

Structure checks do not tell you whether a skill works. After a substantial
change, try it on two or three realistic prompts and confirm the description
still triggers on them and the instructions lead to the right commands. For
skills that call external services, prefer read-only and dry-run paths while
doing this.
