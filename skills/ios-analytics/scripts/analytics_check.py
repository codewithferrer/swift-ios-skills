"""Hygiene, privacy and plan checks on the event catalog.

Every rule here is one that breaks reports silently (a name that is truncated,
a value that explodes cardinality, a reserved name that is dropped, an event
that is logged twice) or leaks data. Limits and reserved names are Google
Analytics 4's for app streams; the sources are in references/firebase-ga4.md.
"""

from __future__ import annotations

import plistlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import analytics_model as am
import swift_source as sw

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
MAX_NAME = 40
MAX_PARAMS = 25
MAX_VALUE = 100
MAX_EVENTS = 500
RESERVED_EVENTS = {
    "ad_activeview", "ad_click", "ad_exposure", "ad_query", "ad_reward", "adunit_exposure", "app_clear_data",
    "app_exception", "app_install", "app_remove", "app_store_refund", "app_update", "app_upgrade",
    "dynamic_link_app_open", "dynamic_link_app_update", "dynamic_link_first_open", "error", "firebase_campaign",
    "firebase_in_app_message_action", "firebase_in_app_message_dismiss", "firebase_in_app_message_impression",
    "first_open", "first_visit", "notification_dismiss", "notification_foreground", "notification_open",
    "notification_receive", "notification_send", "os_update", "session_start", "user_engagement",
}
RESERVED_PARAMS = {"cid", "customer_id", "customerid", "dclid", "gclid", "session_id", "sessionid", "sfmc_id",
                   "sid", "srsltid", "uid", "user_id", "userid"}
RESERVED_PREFIXES = ("_", "firebase_", "ga_", "google_", "gtag.")
AUTOMATIC = {
    "in_app_purchase": "Firebase records it itself when a purchase completes (StoreKit); logging it again double counts",
    "app_store_subscription_convert": "recorded automatically when a trial converts",
    "app_store_subscription_renew": "recorded automatically on renewal",
    "screen_view": None,
}
# Custom names that have a recommended GA4 equivalent, which unlocks built-in reports.
RECOMMENDED = {
    "level_completed": "level_end", "level_complete": "level_end", "level_finished": "level_end",
    "level_started": "level_start", "level_begin": "level_start", "level_start": None,
    "onboarding_complete": "tutorial_complete", "onboarding_completed": "tutorial_complete",
    "onboarding_start": "tutorial_begin", "onboarding_started": "tutorial_begin",
    "tutorial_finished": "tutorial_complete", "tutorial_completed": "tutorial_complete",
    "tutorial_start": "tutorial_begin", "tutorial_started": "tutorial_begin",
    "achievement_unlocked": "unlock_achievement", "score_posted": "post_score", "submit_score": "post_score",
    "share_tapped": "share", "shared": "share", "purchase_completed": "purchase", "signed_in": "login",
    "sign_in": "login", "signed_up": "sign_up", "search_performed": "search",
}
SYNONYMS = (
    {"package_id", "product_id", "sku", "item_id"},
    {"error", "error_message", "error_description", "reason", "failure_reason"},
    {"mode", "game_mode"},
    {"success", "result", "status", "outcome"},
    {"source", "origin", "from", "entry_point", "location"},
    {"screen_name", "screen", "page", "view_name"},
    {"difficulty", "difficulty_level", "level_mode"},
)
PII_NAMES = re.compile(r"(?i)^(email|e_mail|phone|name|first_name|last_name|full_name|username|user_name|address|"
                       r"password|token|ip|ip_address|latitude|longitude|lat|lon|location|birthday|birthdate)$")
BUILT_IN_PARAMS = {"screen_name", "screen_class", "firebase_screen", "firebase_screen_class"}
EVENT_SCOPED_DIMENSIONS = 50
CONFIG_KEYS = {
    "FIREBASE_ANALYTICS_COLLECTION_ENABLED", "FIREBASE_ANALYTICS_COLLECTION_DEACTIVATED",
    "GOOGLE_ANALYTICS_IDFV_COLLECTION_ENABLED", "GOOGLE_ANALYTICS_DEFAULT_ALLOW_AD_PERSONALIZATION_SIGNALS",
    "FirebaseAutomaticScreenReportingEnabled",
}


@dataclass
class Issue:
    severity: str            # error | warning | info
    code: str
    message: str
    where: list = field(default_factory=list)      # ["path:line", ...]


def _loc(root: Path, path: Path, line: int) -> str:
    try:
        return f"{path.relative_to(root)}:{line}"
    except ValueError:
        return f"{path}:{line}"


def read_plists(root: Path) -> dict:
    """Analytics-related keys of every Info.plist under root: {relative path: {key: value}}."""
    found: dict = {}
    for folder, dirs, files in __import__("os").walk(root):
        dirs[:] = [d for d in dirs if d not in sw.EXCLUDED_DIRS]
        for name in files:
            if name == "Info.plist":
                path = Path(folder) / name
                try:
                    data = plistlib.loads(path.read_bytes())
                except Exception:
                    continue
                keys = {k: v for k, v in data.items() if k in CONFIG_KEYS}
                if keys or "CFBundleIdentifier" in data:
                    found[str(path.relative_to(root))] = keys
    return found


def find_privacy_manifests(root: Path) -> list:
    out = []
    for folder, dirs, files in __import__("os").walk(root):
        dirs[:] = [d for d in dirs if d not in sw.EXCLUDED_DIRS]
        out += [Path(folder) / f for f in files if f == "PrivacyInfo.xcprivacy"]
    return out


def check(model: am.Model, plan: dict | None = None) -> list:
    root = model.root
    issues: list = []
    events = model.events()
    methods = model.methods()
    sdk_present = bool(model.imports)
    products = model.firebase_products
    full_product = "FirebaseAnalytics" in products

    # -- names -------------------------------------------------------------------------------
    for name, ev in sorted(events.items()):
        where = [_loc(root, m.file, m.line) for m in ev["methods"]] + \
                [_loc(root, e.file, e.line) for e in ev["raw"]]
        if not NAME_RE.match(name):
            issues.append(Issue("error", "event_name_format",
                                f"'{name}': an event name must start with a letter and use only letters, digits "
                                "and underscores", where))
        if len(name) > MAX_NAME:
            issues.append(Issue("error", "event_name_length",
                                f"'{name}' has {len(name)} characters; the limit is {MAX_NAME}, and a longer name "
                                "is not recorded", where))
        if name in RESERVED_EVENTS or name.startswith(RESERVED_PREFIXES):
            issues.append(Issue("error", "event_reserved",
                                f"'{name}' is reserved by Firebase/GA4 and is dropped or rejected", where))
        if name != name.lower():
            issues.append(Issue("warning", "event_case",
                                f"'{name}' is not lowercase: event names are case sensitive, so 'Game_Over' and "
                                "'game_over' are two events", where))
        if name in AUTOMATIC and AUTOMATIC[name]:
            issues.append(Issue("warning", "event_automatic", f"'{name}': {AUTOMATIC[name]}", where))
        rec = RECOMMENDED.get(name)
        if rec:
            issues.append(Issue("info", "event_recommended",
                                f"'{name}' has a Google-recommended equivalent, '{rec}': using it turns on the "
                                "built-in reports for it", where))
        # parameters
        keys = ev["params"]
        if len(keys) > MAX_PARAMS:
            issues.append(Issue("error", "params_count",
                                f"'{name}' has {len(keys)} parameters; the limit is {MAX_PARAMS}", where))
        for key, kinds in sorted(keys.items()):
            if key.startswith("<"):
                issues.append(Issue("warning", "param_dynamic_key",
                                    f"'{name}': the parameter name {key} is not a literal, so it cannot be "
                                    "registered as a custom dimension", where))
                continue
            if not NAME_RE.match(key) or len(key) > MAX_NAME:
                issues.append(Issue("error", "param_name",
                                    f"'{name}': parameter '{key}' is not a valid name (letters, digits and "
                                    f"underscores, at most {MAX_NAME} characters)", where))
            if key in RESERVED_PARAMS or key.startswith(RESERVED_PREFIXES):
                issues.append(Issue("error", "param_reserved",
                                    f"'{name}': parameter '{key}' is reserved and is ignored", where))
            if PII_NAMES.match(key):
                issues.append(Issue("warning", "param_personal",
                                    f"'{name}': parameter '{key}' sounds like personal data; analytics should "
                                    "carry behaviour, not identity", where))
            if "string-computed" in kinds:
                issues.append(Issue("info", "param_computed",
                                    f"'{name}': '{key}' is computed (a name, title or identifier); confirm it is a "
                                    "closed set and never text a person typed", where))
            if "string-dynamic" in kinds:
                issues.append(Issue("warning", "param_unbounded",
                                    f"'{name}': '{key}' is built from free text (an interpolation, a description "
                                    f"or a name): values over {MAX_VALUE} characters are cut, every distinct "
                                    "value counts toward cardinality, and it can carry personal data", where))

    # parameters whose caller supplies the value
    for method in methods:
        for em in method.emissions:
            for prm in em.params or []:
                if prm.kind != "string-param":
                    continue
                vocab = am.vocabulary(method, prm.expr.strip())
                if vocab["dynamic"] and any(not re.fullmatch(r"[a-z_]\w*", e) or "." in e for e in vocab["dynamic"]):
                    risky = any(re.search(r"localizedDescription|\.description|String\(describing|\\\(", e)
                                for e in vocab["dynamic"])
                    issues.append(Issue(
                        "warning" if risky else "info", "param_caller_dynamic",
                        f"{method.name}({prm.key}): some callers pass a computed value "
                        f"({', '.join(sorted(set(vocab['dynamic']))[:3])}); check it is a small fixed set and never "
                        "user-entered text",
                        [_loc(root, c.file, c.line) for c in method.calls][:6]))

    # same key, different value types
    kinds_by_key: dict = {}
    for name, ev in events.items():
        for key, kinds in ev["params"].items():
            numeric = {k for k in kinds if k in ("int", "double", "bool")}
            if numeric:
                kinds_by_key.setdefault(key, {}).setdefault(name, numeric)
            if any(k.startswith("string") for k in kinds):
                kinds_by_key.setdefault(key, {}).setdefault(name, set()).add("string")
    for key, per_event in sorted(kinds_by_key.items()):
        family = set()
        for ks in per_event.values():
            family |= ks
        if len(family & {"string", "int", "double", "bool"}) > 1:
            issues.append(Issue("warning", "param_type_mix",
                                f"'{key}' is a {'/'.join(sorted(family))} in different events "
                                f"({', '.join(sorted(per_event))}): a custom dimension has one type", []))

    # same idea, different names
    used = {k for ev in events.values() for k in ev["params"]}
    for group in SYNONYMS:
        hit = sorted(used & group)
        if len(hit) > 1:
            issues.append(Issue("info", "param_synonyms",
                                f"{', '.join(repr(h) for h in hit)} look like the same idea under different "
                                "names; pick one so a single dimension answers the question", []))

    # variants of one event
    for name, ev in sorted(events.items()):
        if len(ev["methods"]) > 1:
            sets = [frozenset(k for p in e.params or [] for k in [p.key])
                    for m in ev["methods"] for e in m.emissions if e.event == name]
            common = frozenset.intersection(*sets) if sets else frozenset()
            union = frozenset.union(*sets) if sets else frozenset()
            if union - common:
                issues.append(Issue(
                    "info", "event_variants",
                    f"'{name}' is sent by {len(ev['methods'])} methods with different parameters; "
                    f"{', '.join(sorted(union - common))} exist only on some. Reports on those will have blanks "
                    "for the others: fine when a mode parameter tells them apart",
                    [_loc(root, m.file, m.line) for m in ev["methods"]]))

    # custom dimension budget: every parameter you want in a report needs one of a fixed number of slots
    names = {k for ev in events.values() for k in ev["params"] if not k.startswith("<")} - BUILT_IN_PARAMS
    default_names = set()
    for what, _p, _l, detail in model.config_calls:
        if what == "setDefaultEventParameters":
            default_names |= {part.split("=")[0].strip() for part in detail.split(",") if "=" in part}
    names |= default_names
    if len(names) >= 0.7 * EVENT_SCOPED_DIMENSIONS:
        issues.append(Issue("warning", "dimension_budget",
                            f"{len(names)} distinct parameter names; a standard GA4 property has "
                            f"{EVENT_SCOPED_DIMENSIONS} event-scoped custom dimensions, shared by every app that "
                            "sends to it", []))
    elif len(names) >= 15:
        issues.append(Issue("info", "dimension_budget",
                            f"{len(names)} distinct parameter names; each one you want in a report takes one of the "
                            f"property's {EVENT_SCOPED_DIMENSIONS} event-scoped custom dimensions, shared by every "
                            "app that sends to it", []))

    # -- usage ---------------------------------------------------------------------------------
    dead = [m for m in methods if not m.calls and not m.internal_calls
            and not all(e.debug for e in m.emissions)]
    if dead:
        issues.append(Issue("warning", "event_never_called",
                            f"{len(dead)} analytics method(s) are defined but never called, so their events never "
                            "reach the dashboard",
                            [f"{m.name}  {_loc(root, m.file, m.line)}" for m in dead]))
    if model.raw:
        issues.append(Issue("info", "event_bypass",
                            f"{len(model.raw)} event(s) are sent straight to the SDK from feature code instead of "
                            "through the analytics layer",
                            [_loc(root, e.file, e.line) for e in model.raw]))
    dyn = [e for e in model.emissions if e.event is None]
    for e in dyn:
        if "\\(" in e.event_expr or "+" in e.event_expr:
            issues.append(Issue("error", "event_name_dynamic",
                                "an event name is built at runtime; each distinct name counts toward the 500-event "
                                "cap and none can be registered or reported on", [_loc(root, e.file, e.line)]))
    if len(events) > 0.8 * MAX_EVENTS:
        issues.append(Issue("warning", "event_count",
                            f"{len(events)} distinct events; a property keeps at most {MAX_EVENTS}", []))

    # -- identity and configuration --------------------------------------------------------------
    for what, path, line, _detail in model.config_calls:
        if what in ("setUserID", "setUserId"):
            issues.append(Issue("warning", "identity_user_id",
                                "a user ID is set: it links events to a person; use it only with a stable "
                                "pseudonymous id and with the user's consent", [_loc(root, path, line)]))
        if what == "setUserProperty":
            issues.append(Issue("info", "identity_user_property",
                                "a user property is set: check it is a bucket (plan, locale), not an identity",
                                [_loc(root, path, line)]))
    plists = read_plists(root)
    manual_screen = "screen_view" in events
    auto_state = {p: k.get("FirebaseAutomaticScreenReportingEnabled") for p, k in plists.items()
                  if "FirebaseAutomaticScreenReportingEnabled" in k}
    if "Firebase Analytics" in model.imports or "Firebase Analytics (no IDFA)" in model.imports:
        auto_on = not auto_state or any(v is not False for v in auto_state.values())
        if manual_screen and auto_on:
            issues.append(Issue("warning", "screen_view_double",
                                "screens are logged by hand and Firebase's automatic screen reporting is not "
                                "switched off: every screen is counted twice, once with a UIHostingController name",
                                list(plists)))
        if not manual_screen and auto_on:
            issues.append(Issue("info", "screen_view_automatic",
                                "no screen_view is logged by hand and automatic reporting is on: in a SwiftUI app "
                                "it names screens after hosting controllers, not after your views", list(plists)))
        if not manual_screen and not auto_on:
            issues.append(Issue("warning", "screen_view_none",
                                "automatic screen reporting is off and no screen_view is logged: there is no "
                                "screen data at all", list(plists)))
    if "Firebase Analytics" in model.imports or products:
        keys = {k for p in plists.values() for k in p}
        if full_product or "FirebaseAnalyticsIdentitySupport" in products:
            linked = [p for p in ("FirebaseAnalytics", "FirebaseAnalyticsIdentitySupport") if p in products]
            issues.append(Issue("info", "identity_product",
                                f"{' and '.join(linked)} {'is' if len(linked) == 1 else 'are'} linked: that "
                                "includes the identity-support module, which can read the advertising identifier. "
                                "FirebaseAnalyticsCore alone is analytics without it; if the app does not use "
                                "ad-related reports, Core is enough", []))
        if "GOOGLE_ANALYTICS_IDFV_COLLECTION_ENABLED" not in keys:
            issues.append(Issue("info", "identity_idfv",
                                "Firebase Analytics collects the vendor identifier (IDFV) unless "
                                "GOOGLE_ANALYTICS_IDFV_COLLECTION_ENABLED is NO in Info.plist", list(plists)))
    for label in ("AppTrackingTransparency", "AdSupport (IDFA)"):
        if label in model.imports:
            issues.append(Issue("info", "identity_tracking",
                                f"{label} is imported: confirm it is needed; an app that does not track across "
                                "other companies' apps and sites needs neither",
                                [str(p.relative_to(root)) for p in model.imports[label]][:4]))
    if sdk_present and not find_privacy_manifests(root):
        issues.append(Issue("warning", "privacy_manifest",
                            "no PrivacyInfo.xcprivacy in the project: since 1 May 2024 App Store Connect expects the "
                            "app to declare an approved reason for each required-reason API it uses (UserDefaults "
                            "is the common one) and its collected data types", []))

    old_arg = [(sf.path, sf.line(q)) for sf in model.files for q, lit in sf.strings.items()
               if lit.value == "-FIRAnalyticsDebugEnabled"]
    if old_arg:
        issues.append(Issue("info", "debugview_argument",
                            "the launch argument -FIRAnalyticsDebugEnabled is the older spelling; Firebase's "
                            "DebugView page documents -FIRDebugEnabled. If DebugView stays empty, try that one",
                            [_loc(root, p, l) for p, l in old_arg]))

    # -- plan ------------------------------------------------------------------------------------
    if plan is not None:
        for issue in compare_plan(model, plan):
            issues.append(issue)

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: order[i.severity])
    return issues


def compare_plan(model: am.Model, plan: dict) -> list:
    events = model.events()
    root = model.root
    out: list = []
    missing = [n for n in plan if n not in events]
    if missing:
        out.append(Issue("warning", "plan_not_implemented",
                         f"{len(missing)} planned event(s) are not in the code",
                         [f"{n}  (plan line {plan[n]['line']})" for n in missing]))
    extra = [n for n in events if n not in plan]
    if extra:
        out.append(Issue("warning", "plan_not_planned",
                         f"{len(extra)} event(s) in the code are not in the plan",
                         [f"{n}  {_loc(root, *( (events[n]['methods'][0].file, events[n]['methods'][0].line) if events[n]['methods'] else (events[n]['raw'][0].file, events[n]['raw'][0].line)))}"
                          for n in sorted(extra)]))
    for name, spec in plan.items():
        if name in events and spec["params"]:
            have = set(events[name]["params"])
            miss = [p for p in spec["params"] if p not in have]
            more = sorted(k for k in have if k not in spec["params"] and not k.startswith("<"))
            if miss:
                out.append(Issue("warning", "plan_param_missing",
                                 f"'{name}' lacks planned parameter(s) {', '.join(miss)}", []))
            if more:
                out.append(Issue("info", "plan_param_extra",
                                 f"'{name}' sends parameter(s) the plan does not list: {', '.join(more)}", []))
    return out
