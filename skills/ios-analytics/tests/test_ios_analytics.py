"""Offline tests: a synthetic Swift project that exercises every layer of the scan."""

from __future__ import annotations

import contextlib
import io
import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analytics_check as ck  # noqa: E402
import analytics_coverage as ac  # noqa: E402
import analytics_model as am  # noqa: E402
import analytics_plan as ap  # noqa: E402
import analytics_scan as scan  # noqa: E402
import swift_source as sw  # noqa: E402

FACADE = '''
import FirebaseAnalytics
import Foundation

enum AnalyticsEvent: String {
    case gameStart = "game_start"
    case levelCleared
}

final class AnalyticsManager {
    static let shared = AnalyticsManager()
    private init() {}

    /// Funnel: paywallShown -> purchaseFailed
    private func log(_ name: String, _ parameters: [String: Any]) {
        Analytics.logEvent(name, parameters: parameters)
    }

    func configure() {
        Analytics.setDefaultEventParameters(["app_name": "demo"])
    }

    func gameStart(difficulty: Difficulty, palette: String) {
        log(AnalyticsEvent.gameStart.rawValue, ["difficulty": difficulty.analyticsName, "palette": palette])
    }

    func gameOver(score: Int, won: Bool, message: String) {
        log("game_over", ["score": score, "won": won, "detail": "got \\(message)"])
    }

    func paywallShown(source: String) {
        log("paywall_shown", ["source": source])
    }

    func purchaseFailed(error: Error) {
        log("purchase_failed", ["error": error.localizedDescription])
    }

    func screenView(_ name: String) {
        log("screen_view", ["screen_name": name, "screen_class": name])
    }

    func settingChanged(name: String, value: String) {
        log("setting_changed", ["name": name, "value": value])
    }

    func levelCleared(level: Int) {
        log(AnalyticsEvent.levelCleared.rawValue, ["level": level, "user_id": "x", "Result": true])
    }

    func neverCalled() {
        log("never_called_event", [:])
    }

    func levelUp() {
        Analytics.logEvent(AnalyticsEventLevelUp, parameters: [AnalyticsParameterLevel: 3])
    }

    #if DEBUG
    func debugCheck() {
        Analytics.logEvent("debug_check", parameters: nil)
    }
    #endif
}
'''

VIEWMODEL = '''
import Foundation
import UserNotifications

enum Difficulty: String { case easy, hard
    var analyticsName: String { rawValue } }

final class GameViewModel: ObservableObject {
    @Published var soundEnabled = true
    @Published var difficulty = Difficulty.easy {
        didSet { AnalyticsManager.shared.settingChanged(name: "difficulty", value: difficulty.analyticsName) }
    }
    var store = Store()

    func startGame() {
        AnalyticsManager.shared.gameStart(difficulty: difficulty, palette: "classic")
    }

    func finish(score: Int) {
        AnalyticsManager.shared.gameOver(score: score, won: true, message: "ok")
    }

    func deleteProgress() {
        store.wipe()
    }

    func restorePurchases() {
        Task { try? await Purchases.shared.restorePurchases() }
    }

    func load() {
        do { try store.load() } catch { print(error) }
    }

    func save() {
        do { try store.save() } catch { AnalyticsManager.shared.purchaseFailed(error: error) }
    }

    func parse() {
        do { try store.parse() } catch { }
    }

    func requestNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert]) { granted, _ in }
    }

    func buy() {
        Purchases.shared.purchase(package: pkg)
    }
}
'''

VIEWS = '''
import SwiftUI

struct HomeView: View {
    @StateObject var vm = GameViewModel()
    @State private var showSettings = false
    var onClose: () -> Void = {}

    var body: some View {
        VStack {
            Button("Start game") { vm.startGame() }
            Button("Delete progress") { vm.deleteProgress() }
            Button("OK") { vm.deleteProgress() }
            Button("Close") { onClose() }
            Toggle("Sound", isOn: $vm.soundEnabled)
            Picker("Difficulty", selection: $vm.difficulty) { Text("Easy") }
            Button(action: vm.restorePurchases) { Text("Restore purchases") }
            Button { vm.finish(score: 1) } label: { Label("Finish", systemImage: "flag") }
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .navigationDestination(for: Int.self) { _ in PaywallView(source: "home") }
    }
}

struct PaywallView: View {
    let source: String
    var body: some View {
        Text("Pro").onAppear { AnalyticsManager.shared.paywallShown(source: source) }
    }
}

struct SettingsView: View {
    var body: some View {
        Button("Reset") { reset() }
    }
    func reset() {}
}

struct LonelyView: View {
    var body: some View { Button("Share") { } }
}
'''

ROUTER = '''
enum AppDestination: Hashable { case settings }
final class Router {
    func navigate(to destination: AppDestination) {
        switch destination {
        case .settings: AnalyticsManager.shared.screenView("settings")
        }
    }
}
'''

OTHER = '''
import FirebaseAnalytics
final class Sync {
    func done(n: Int) {
        Analytics.logEvent("sync_done", parameters: nil)
        Analytics.logEvent("level_\\(n)", parameters: nil)
    }
}
'''

INTENTS = '''
import AppIntents
struct StopIntent: AppIntent {
    static var title: LocalizedStringResource = "Stop"
    func perform() async throws -> some IntentResult { .result() }
}
struct LogIntent: AppIntent {
    static var title: LocalizedStringResource = "Log"
    func perform() async throws -> some IntentResult {
        AnalyticsManager.shared.paywallShown(source: "intent")
        return .result()
    }
}
'''

PLAN = '''# Product

## 7. Analytics

| Event | Parameters |
|---|---|
| `game_start` | `difficulty`, `palette` |
| `game_over` | `score`, `won`, `reason` |
| `level_complete` | `level` |
| `paywall_shown` | `source` |
'''


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_project(root: Path, plist: dict | None = None, manifest: bool = False) -> None:
    write(root, "App/Analytics/AnalyticsManager.swift", FACADE)
    write(root, "App/GameViewModel.swift", VIEWMODEL)
    write(root, "App/Views.swift", VIEWS)
    write(root, "App/Router.swift", ROUTER)
    write(root, "App/Sync.swift", OTHER)
    write(root, "App/Intents.swift", INTENTS)
    write(root, "docs/PRODUCT.md", PLAN)
    data = {"CFBundleIdentifier": "demo.app"}
    data.update(plist if plist is not None else {})
    (root / "App").mkdir(exist_ok=True)
    (root / "App/Info.plist").write_bytes(plistlib.dumps(data))
    if manifest:
        write(root, "App/PrivacyInfo.xcprivacy", "<plist/>")


class Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        build_project(self.root)
        self.model = am.scan(self.root)

    def codes(self, issues, severity=None):
        return {i.code for i in issues if severity is None or i.severity == severity}


class ReaderTests(unittest.TestCase):
    def test_comments_and_strings_cannot_fool_the_matching(self):
        text = '''
// func fake() { }
/* struct Fake { } */
func real() { let s = "}{ func nope() {"; let r = #"raw " } "#; let m = """
    multi } line
    """ }
struct After { func ok() {} }
'''
        sf = sw.parse("x.swift", text)
        names = [(d.name, d.owner) for d in sf.funcs]
        self.assertEqual(names, [("real", None), ("ok", "After")])
        self.assertEqual([d.name for d in sf.types], ["After"])

    def test_interpolation_with_quotes_and_parens(self):
        text = 'func a() { let s = "x \\(foo("}")) y"; b() }\nfunc b() {}\n'
        sf = sw.parse("x.swift", text)
        self.assertEqual([d.name for d in sf.funcs], ["a", "b"])
        a = sf.funcs[0]
        self.assertEqual([c.name for c in sw.calls_in(sf, *a.body)], ["b"])

    def test_receivers_including_chains_with_calls(self):
        text = '''func f() {
    UNUserNotificationCenter.current().requestAuthorization(options: [.alert]) { _, _ in }
    self.manager?.log("a")
    AnalyticsManager.shared.paywallShown(source: "s")
}'''
        sf = sw.parse("x.swift", text)
        got = {(c.receiver, c.name) for c in sw.calls_in(sf, 0, len(sf.code))}
        self.assertIn(("UNUserNotificationCenter.current()", "requestAuthorization"), got)
        self.assertIn(("self.manager", "log"), got)
        self.assertIn(("AnalyticsManager.shared", "paywallShown"), got)

    def test_declarations_are_not_calls(self):
        sf = sw.parse("x.swift", "enum E { case daily(Int) }\nfunc purchase(a: Int) { }\n")
        self.assertEqual([c.name for c in sw.calls_in(sf, 0, len(sf.code))], [])

    def test_property_observers_get_the_property_name(self):
        sf = sw.parse("x.swift", "final class A { var x = 0 { didSet { go() } }\n func go() {} }")
        self.assertIn("x.didSet", [d.name for d in sf.funcs])

    def test_arguments_labels_and_literals(self):
        sf = sw.parse("x.swift", 'func g() { log("evt", parameters: ["a": 1], flag: true) }')
        call = sw.calls_in(sf, 0, len(sf.code))[0]
        args = sw.split_args(sf, call.open_paren)
        self.assertEqual([a[0] for a in args], [None, "parameters", "flag"])
        self.assertEqual(sw.first_string(sf, args[0][1], args[0][2]).value, "evt")
        self.assertEqual(sf.line(call.start), 1)


class ModelTests(Project):
    def test_events_and_methods(self):
        events = self.model.events()
        for name in ("game_start", "game_over", "paywall_shown", "purchase_failed", "screen_view", "setting_changed",
                     "levelCleared", "never_called_event", "level_up", "sync_done"):
            self.assertIn(name, events, name)
        self.assertNotIn("debug_check", events)
        self.assertIn("debug_check", self.model.events(include_debug=True))

    def test_enum_and_firebase_constants_resolve(self):
        events = self.model.events()
        self.assertEqual({m.name for m in events["game_start"]["methods"]}, {"gameStart"})
        self.assertIn("level", events["level_up"]["params"])

    def test_the_wrapper_is_plumbing_not_an_event(self):
        self.assertTrue(self.model.bridges)
        self.assertNotIn(None, self.model.events())

    def test_parameter_kinds(self):
        params = self.model.events()["game_over"]["params"]
        self.assertEqual(params["score"], {"int"})
        self.assertEqual(params["won"], {"bool"})
        self.assertEqual(params["detail"], {"string-dynamic"})
        self.assertEqual(self.model.events()["game_start"]["params"]["difficulty"], {"string-enum"})
        self.assertEqual(self.model.events()["paywall_shown"]["params"]["source"], {"string-param"})

    def test_call_sites_and_never_called(self):
        by_name = {m.name: m for m in self.model.methods()}
        self.assertEqual(len(by_name["gameStart"].calls), 1)
        self.assertEqual(len(by_name["paywallShown"].calls), 2)
        self.assertEqual(by_name["neverCalled"].calls, [])

    def test_facade_and_raw_emissions(self):
        self.assertEqual(list(self.model.facades), ["AnalyticsManager"])
        self.assertEqual([e.event for e in self.model.raw], ["sync_done", None])

    def test_default_parameters_are_read(self):
        details = [d for w, _, _, d in self.model.config_calls if w == "setDefaultEventParameters"]
        self.assertEqual(details, ['app_name = "demo"'])

    def test_vocabulary_of_a_caller_supplied_parameter(self):
        method = next(m for m in self.model.methods() if m.name == "paywallShown")
        vocab = am.vocabulary(method, "source")
        self.assertEqual(vocab["literals"], {"intent": 1})
        self.assertEqual(vocab["dynamic"], ["source"])

    def test_excluded_paths_are_skipped(self):
        model = am.scan(self.root, ("Intents",))
        paywall = next(m for m in model.methods() if m.name == "paywallShown")
        self.assertEqual(len(paywall.calls), 1)

    def test_the_plan_table_is_parsed(self):
        plan = am.parse_plan(self.root / "docs/PRODUCT.md")
        self.assertEqual(list(plan), ["game_start", "game_over", "level_complete", "paywall_shown"])
        self.assertEqual(plan["game_over"]["params"], ["score", "won", "reason"])
        self.assertEqual(plan["paywall_shown"]["section"], "7. Analytics")


class CoverageTests(Project):
    def setUp(self):
        super().setUp()
        self.cov = ac.analyse(self.model)

    def control(self, label):
        return next(c for c in self.cov.controls if c.label == label)

    def test_a_control_is_covered_through_the_functions_it_calls(self):
        self.assertEqual(self.control("Start game").status, "covered")
        self.assertEqual(self.control("Finish").status, "covered")

    def test_uncovered_and_its_category(self):
        c = self.control("Delete progress")
        self.assertEqual((c.status, c.category, c.priority), ("uncovered", "destructive", "high"))

    def test_navigation_labels_outrank_callee_names(self):
        self.assertEqual(self.control("OK").category, "navigation")

    def test_closure_handed_by_the_parent_is_delegated(self):
        self.assertEqual(self.control("Close").status, "delegated")

    def test_bindings_are_covered_by_observers(self):
        self.assertEqual(self.control("Difficulty").status, "covered")
        self.assertEqual(self.control("Sound").status, "uncovered")

    def test_action_given_by_reference(self):
        self.assertEqual(self.control("Restore purchases").status, "uncovered")

    def test_screens_and_tracking(self):
        views = {v.name: v for v in self.cov.views}
        self.assertTrue(views["PaywallView"].tracked)
        self.assertTrue(views["SettingsView"].tracked)
        self.assertIn("settings", views["SettingsView"].evidence)
        self.assertFalse(views["HomeView"].tracked)
        self.assertEqual([h for _, _, h in views["SettingsView"].presented], ["sheet"])
        self.assertEqual(views["LonelyView"].presented, [])

    def test_catches(self):
        notes = {c.func: c.note for c in self.cov.catches}
        self.assertEqual(notes["load"], "logged locally only")
        self.assertEqual(notes["parse"], "no record")
        self.assertNotIn("save", notes)

    def test_entry_points(self):
        kinds = {(s.kind, s.func): s.covered for s in self.cov.sites}
        self.assertFalse(kinds[("permission prompt", "requestNotifications")])
        self.assertFalse(kinds[("purchase flow", "buy")])
        intents = {s.owner: s.covered for s in self.cov.intents}
        self.assertEqual(intents, {"StopIntent": False, "LogIntent": True})

    def test_a_site_is_covered_by_the_caller_that_records(self):
        write(self.root, "App/Wrapper.swift", '''
final class PurchaseService { func purchase() { Purchases.shared.purchase(package: p) } }
final class Flow {
    func go() {
        AnalyticsManager.shared.paywallShown(source: "x")
        PurchaseService().purchase()
    }
}''')
        cov = ac.analyse(am.scan(self.root))
        site = next(s for s in cov.sites if s.owner == "PurchaseService")
        self.assertTrue(site.covered)
        self.assertEqual(site.note, "recorded by the caller")


class CheckTests(Project):
    def test_each_hygiene_rule(self):
        issues = ck.check(self.model)
        codes = self.codes(issues)
        for code in ("event_case", "param_reserved", "param_unbounded", "event_never_called", "event_bypass",
                     "event_name_dynamic", "param_caller_dynamic", "privacy_manifest"):
            self.assertIn(code, codes, code)
        self.assertEqual(self.codes(issues, "error"), {"event_name_dynamic", "param_reserved"})

    def test_debug_only_methods_are_not_reported_as_dead(self):
        issue = next(i for i in ck.check(self.model) if i.code == "event_never_called")
        listed = " ".join(issue.where)
        self.assertIn("neverCalled", listed)
        self.assertNotIn("debugCheck", listed)

    def test_reserved_and_format_rules(self):
        write(self.root, "App/More.swift", '''
final class More {
    func a() { Analytics.logEvent("first_open", parameters: nil) }
    func b() { Analytics.logEvent("1bad name", parameters: nil) }
    func c() { Analytics.logEvent("this_event_name_is_much_longer_than_forty_chars", parameters: nil) }
    func d() { Analytics.logEvent("in_app_purchase", parameters: nil) }
}''')
        issues = ck.check(am.scan(self.root))
        codes = self.codes(issues)
        self.assertTrue({"event_reserved", "event_name_format", "event_name_length", "event_automatic"} <= codes)

    def test_custom_names_with_a_recommended_equivalent(self):
        write(self.root, "App/Rec.swift",
              'final class Rec { func a() { Analytics.logEvent("onboarding_complete", parameters: nil) } }')
        issues = ck.check(am.scan(self.root))
        rec = next(i for i in issues if i.code == "event_recommended")
        self.assertIn("tutorial_complete", rec.message)

    def test_type_mix_and_synonyms(self):
        write(self.root, "App/Mix.swift", '''
final class Mix {
    func a() { Analytics.logEvent("a_event", parameters: ["score": 1, "mode": "x"]) }
    func b() { Analytics.logEvent("b_event", parameters: ["score": "high", "game_mode": "y"]) }
}''')
        codes = self.codes(ck.check(am.scan(self.root)))
        self.assertTrue({"param_type_mix", "param_synonyms"} <= codes)

    def test_screen_view_configuration(self):
        base = ck.check(self.model)
        self.assertIn("screen_view_double", self.codes(base))
        write(self.root, "App/Info.plist", plistlib.dumps(
            {"CFBundleIdentifier": "d", "FirebaseAutomaticScreenReportingEnabled": False}).decode("latin-1"))
        (self.root / "App/Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleIdentifier": "d", "FirebaseAutomaticScreenReportingEnabled": False}))
        self.assertNotIn("screen_view_double", self.codes(ck.check(am.scan(self.root))))

    def test_privacy_manifest_presence_silences_the_warning(self):
        write(self.root, "App/PrivacyInfo.xcprivacy", "<plist/>")
        self.assertNotIn("privacy_manifest", self.codes(ck.check(am.scan(self.root))))

    def test_user_id_is_flagged(self):
        write(self.root, "App/Id.swift", 'final class Id { func a() { Analytics.setUserID("abc") } }')
        self.assertIn("identity_user_id", self.codes(ck.check(am.scan(self.root))))

    def test_plan_comparison(self):
        plan = am.parse_plan(self.root / "docs/PRODUCT.md")
        issues = ck.check(self.model, plan)
        by_code = {i.code: i for i in issues}
        self.assertIn("level_complete", " ".join(by_code["plan_not_implemented"].where))
        self.assertIn("plan_not_planned", by_code)
        self.assertNotIn("debug_check", " ".join(by_code["plan_not_planned"].where))
        self.assertIn("reason", by_code["plan_param_missing"].message)

    def test_a_clean_project_has_no_errors(self):
        clean = self.root / "clean"
        write(clean, "A.swift", '''
import FirebaseAnalytics
final class AnalyticsManager {
    static let shared = AnalyticsManager()
    func a() { Analytics.logEvent("game_start", parameters: ["mode": "classic"]) }
    func b() { Analytics.logEvent("game_over", parameters: ["score": 3]) }
    func c() { Analytics.logEvent("level_up", parameters: ["level": 3]) }
}
struct V { func go() { AnalyticsManager.shared.a(); AnalyticsManager.shared.b(); AnalyticsManager.shared.c() } }''')
        (clean / "Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleIdentifier": "c", "FirebaseAutomaticScreenReportingEnabled": False}))
        write(clean, "PrivacyInfo.xcprivacy", "<plist/>")
        issues = ck.check(am.scan(clean))
        self.assertEqual(self.codes(issues, "error") | self.codes(issues, "warning"), {"screen_view_none"})


class PlanTests(Project):
    def test_candidates_point_at_the_right_places(self):
        cov = ac.analyse(self.model)
        top = lambda event: [c.signature.split("(")[0] for c in ap.candidates_for(self.model, cov, event)]
        self.assertIn("GameViewModel.finish", top("game_finished") + top("game_completed"))
        self.assertTrue(any("StopIntent" in s for s in top("intent_invoked")))
        self.assertIn("PaywallView", top("paywall_shown"))
        self.assertEqual(top("zeppelin_launched"), [])

    def test_verbs_are_matched_by_meaning(self):
        self.assertEqual(ap.tokens("alarmCreated_x"), ["alarm", "created", "x"])
        self.assertIn("save", ap._canon("created"))


class CliTests(Project):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["analytics_scan.py", *map(str, argv)]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = scan.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                err.write(str(exc.code))
        return code, out.getvalue(), err.getvalue()

    def test_inventory(self):
        code, out, _ = self.run_cli("inventory", "--root", self.root)
        self.assertEqual(code, 0)
        self.assertIn("Facade: AnalyticsManager", out)
        self.assertIn("paywall_shown", out)
        self.assertIn("Defined but never called", out)
        self.assertIn('app_name = "demo"', out)

    def test_inventory_json(self):
        code, out, _ = self.run_cli("inventory", "--root", self.root, "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn("game_start", {e["event"] for e in data["events"]})
        self.assertTrue(any(e["debug_only"] for e in data["events"]))

    def test_coverage(self):
        code, out, _ = self.run_cli("coverage", "--root", self.root)
        self.assertEqual(code, 0)
        self.assertIn("Delete progress", out)
        self.assertIn("permission prompt", out)
        self.assertIn("Errors that are caught and not recorded", out)

    def test_check_exit_codes(self):
        code, out, _ = self.run_cli("check", "--root", self.root)
        self.assertEqual(code, 1)
        self.assertIn("event_name_dynamic", out)

    def test_plan(self):
        code, out, _ = self.run_cli("plan", "--root", self.root, "--plan", self.root / "docs/PRODUCT.md")
        self.assertEqual(code, 0)
        self.assertIn("[TODO] level_complete", out)
        self.assertIn("[ok  ] game_start", out)
        code, _, _ = self.run_cli("plan", "--root", self.root, "--plan", self.root / "docs/PRODUCT.md", "--strict")
        self.assertEqual(code, 1)

    def test_errors_are_clear(self):
        code, _, err = self.run_cli("inventory", "--root", self.root / "missing")
        self.assertNotEqual(code, 0)
        self.assertIn("not a folder", err)
        empty = self.root / "empty"
        empty.mkdir()
        code, _, err = self.run_cli("coverage", "--root", empty)
        self.assertNotEqual(code, 0)
        self.assertIn("no Swift files", err)

    def test_a_project_without_events_says_so(self):
        bare = self.root / "bare"
        write(bare, "V.swift", 'import SwiftUI\nstruct V: View { var body: some View { Button("Go") { go() } }\n func go() {} }')
        code, out, _ = self.run_cli("coverage", "--root", bare)
        self.assertEqual(code, 0)
        self.assertIn("No events are recorded anywhere yet", out)


if __name__ == "__main__":
    unittest.main()
