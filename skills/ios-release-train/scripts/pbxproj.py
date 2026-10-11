"""Reading and editing the release-relevant build settings of an Xcode
project.pbxproj: the marketing version and the build number, and which
targets share them.

Why a parser and not `grep MARKETING_VERSION | head -1`: a project with a
watch app, widgets or test targets has the setting in many places with
different values, and the first match is whichever happens to come first in
the file. Here the settings are tied to the target and configuration they
belong to, so "the app's version" is a question with an answer, and a bump
edits exactly the lines that carry the app's build number and nothing else.

Pure standard library, no Xcode needed. `xcodebuild -showBuildSettings` is
still the authority when it is available, because settings can also come from
an .xcconfig file this parser cannot see; callers compare the two.

Edits are made line by line on the original text, so everything that is not
the value of a setting is left byte for byte as it was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

OBJECT_START = re.compile(r"^\t\t([0-9A-Fa-f]{24}) /\* (.*?) \*/ = \{\s*$")
OBJECT_END = "\t\t};"
SETTING_LINE = re.compile(r"^(\t{4})([A-Za-z_][A-Za-z0-9_\[\]=*\-]*) = (.+);\s*$")
IOS_APP = "com.apple.product-type.application"

VERSION_KEY = "MARKETING_VERSION"
BUILD_KEY = "CURRENT_PROJECT_VERSION"


def unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


@dataclass
class Setting:
    key: str
    value: str
    line: int  # index into Project.lines


@dataclass
class BuildConfig:
    id: str
    name: str
    settings: dict = field(default_factory=dict)
    has_xcconfig: bool = False


@dataclass
class Target:
    id: str
    name: str
    product_type: str
    config_list_id: str


@dataclass
class Edit:
    config_id: str
    owner: str        # the target's name, or "(project)" for project-level settings
    configuration: str
    key: str
    line: int
    old: str
    new: str


class Project:
    def __init__(self, path: Path, text: str):
        self.path = path
        self.lines = text.splitlines(keepends=True)
        self.targets: list[Target] = []
        self.config_lists: dict[str, list[str]] = {}
        self.configs: dict[str, BuildConfig] = {}
        self.project_config_list: str | None = None
        self._parse()

    @classmethod
    def load(cls, path: Path) -> "Project":
        pbxproj = path / "project.pbxproj" if path.suffix == ".xcodeproj" else path
        return cls(pbxproj, pbxproj.read_text(encoding="utf-8"))

    # -- parsing ---------------------------------------------------------------

    def _parse(self) -> None:
        i = 0
        n = len(self.lines)
        while i < n:
            m = OBJECT_START.match(self.lines[i].rstrip("\n"))
            if not m:
                i += 1
                continue
            object_id, start = m.group(1), i
            j = i + 1
            while j < n and self.lines[j].rstrip("\n") != OBJECT_END:
                j += 1
            self._read_object(object_id, start, j)
            i = j + 1

    def _read_object(self, object_id: str, start: int, end: int) -> None:
        body = self.lines[start + 1:end]
        text = "".join(body)
        isa = re.search(r"\bisa = (\w+);", text)
        if not isa:
            return
        kind = isa.group(1)

        def field_value(name: str) -> str | None:
            found = re.search(rf"^\t\t\t{name} = (.+?);\s*$", text, re.M)
            return unquote(found.group(1)) if found else None

        if kind == "PBXNativeTarget":
            list_ref = re.search(r"buildConfigurationList = ([0-9A-Fa-f]{24})", text)
            self.targets.append(Target(
                id=object_id,
                name=field_value("name") or "",
                product_type=field_value("productType") or "",
                config_list_id=list_ref.group(1) if list_ref else "",
            ))
        elif kind == "PBXProject":
            list_ref = re.search(r"buildConfigurationList = ([0-9A-Fa-f]{24})", text)
            self.project_config_list = list_ref.group(1) if list_ref else None
        elif kind == "XCConfigurationList":
            block = re.search(r"buildConfigurations = \((.*?)\);", text, re.S)
            ids = re.findall(r"([0-9A-Fa-f]{24}) /\*", block.group(1)) if block else []
            self.config_lists[object_id] = ids
        elif kind == "XCBuildConfiguration":
            config = BuildConfig(id=object_id, name=field_value("name") or "",
                                 has_xcconfig="baseConfigurationReference" in text)
            in_settings = False
            for offset, raw in enumerate(body):
                line = raw.rstrip("\n")
                if line == "\t\t\tbuildSettings = {":
                    in_settings = True
                    continue
                if in_settings and line == "\t\t\t};":
                    break
                if in_settings:
                    m = SETTING_LINE.match(line)
                    if m and not m.group(3).endswith("("):
                        config.settings[m.group(2)] = Setting(m.group(2), unquote(m.group(3)), start + 1 + offset)
            self.configs[object_id] = config

    # -- queries -----------------------------------------------------------------

    def target_config(self, target: Target, configuration: str) -> BuildConfig | None:
        for config_id in self.config_lists.get(target.config_list_id, []):
            if self.configs[config_id].name == configuration:
                return self.configs[config_id]
        return None

    def project_config(self, configuration: str) -> BuildConfig | None:
        for config_id in self.config_lists.get(self.project_config_list or "", []):
            if self.configs[config_id].name == configuration:
                return self.configs[config_id]
        return None

    def resolve(self, target: Target, configuration: str, key: str) -> Setting | None:
        """The setting as it applies to `target`: its own, else the project's.
        $(inherited) tricks and .xcconfig files are not followed.
        """
        for config in (self.target_config(target, configuration), self.project_config(configuration)):
            if config and key in config.settings:
                return config.settings[key]
        return None

    def owner_of(self, config_id: str) -> str:
        for target in self.targets:
            if config_id in self.config_lists.get(target.config_list_id, []):
                return target.name
        return "(project)"

    def app_targets(self, configuration: str = "Release") -> list[Target]:
        """iOS app targets: application products that are not watch apps."""
        found = []
        for target in self.targets:
            if target.product_type != IOS_APP:
                continue
            sdk = self.resolve(target, configuration, "SDKROOT")
            family = self.resolve(target, configuration, "TARGETED_DEVICE_FAMILY")
            config = self.target_config(target, configuration)
            watch_only = bool(config and "WATCHOS_DEPLOYMENT_TARGET" in config.settings
                              and "IPHONEOS_DEPLOYMENT_TARGET" not in config.settings)
            if (sdk and sdk.value.startswith("watch")) or (family and family.value.strip() == "4") or watch_only:
                continue
            found.append(target)
        return found

    def sharing(self, key: str, value: str) -> list[Edit]:
        """Every build configuration that sets `key` to `value`, as no-op
        edits describing where the value lives.
        """
        out = []
        for config_id, config in self.configs.items():
            setting = config.settings.get(key)
            if setting and setting.value == value:
                out.append(Edit(config_id, self.owner_of(config_id), config.name, key, setting.line,
                                self.lines[setting.line], self.lines[setting.line]))
        return out

    # -- edits -------------------------------------------------------------------

    def plan_change(self, key: str, old: str, new: str) -> list[Edit]:
        """Edits that change `key` from `old` to `new` wherever it is `old`,
        keeping the original quoting. Other values are left alone.
        """
        edits = []
        for edit in self.sharing(key, old):
            line = self.lines[edit.line]
            replaced = re.sub(rf"({re.escape(key)} = )(\"?){re.escape(old)}(\"?);",
                              lambda m: f"{m.group(1)}{m.group(2)}{new}{m.group(3)};", line, count=1)
            edits.append(Edit(edit.config_id, edit.owner, edit.configuration, key, edit.line, line, replaced))
        return edits

    def apply(self, edits: list[Edit]) -> str:
        lines = list(self.lines)
        for edit in edits:
            lines[edit.line] = edit.new
        return "".join(lines)


def load(path: str | Path) -> Project:
    return Project.load(Path(path))
