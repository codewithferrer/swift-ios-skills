"""Offline tests for the purchase texts: reading the files, the diff against
App Store Connect, the push script's flow, the client's request payloads and
the audit's subscription rows.

    python3 -m unittest discover -s tests -v

Nothing here touches the network or needs credentials. The client tests are
skipped when requests/PyJWT are not installed.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import asc_metadata as md  # noqa: E402
import asc_purchases as pur  # noqa: E402
import audit_localizations  # noqa: E402
import push_purchases  # noqa: E402

try:
    import asc_client  # noqa: E402

    HAVE_CLIENT = True
except ImportError:
    HAVE_CLIENT = False

LIFETIME = "com.example.app.premium.lifetime"
MONTHLY = "com.example.app.premium.monthly"
GROUP = "Premium"


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def loc(locale: str, state: str = "PREPARE_FOR_SUBMISSION", **attributes) -> dict:
    return {"id": f"loc-{locale}", "attributes": {"locale": locale, "state": state, **attributes}}


class FakeError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FakeClient:
    def __init__(self, iaps=(), subs=(), groups=(), locs=None):
        self.iaps, self.subs, self.groups = list(iaps), list(subs), list(groups)
        self.locs = locs or {}   # (kind, parent id) -> {locale: resource}
        self.calls = []
        self.fail_on_write = False

    def list_in_app_purchases(self, app_id):
        self.calls.append(("list_iaps",))
        return self.iaps

    def list_subscriptions(self, app_id):
        self.calls.append(("list_subs",))
        return self.subs

    def list_subscription_groups(self, app_id):
        self.calls.append(("list_groups",))
        return self.groups

    def list_purchase_localizations(self, kind, parent_id):
        return dict(self.locs.get((kind, parent_id), {}))

    def create_purchase_localization(self, kind, parent_id, locale, attributes):
        if self.fail_on_write:
            raise FakeError("409 conflict", status_code=409)
        self.calls.append(("create", kind, parent_id, locale, dict(attributes)))

    def update_purchase_localization(self, kind, localization_id, attributes):
        if self.fail_on_write:
            raise FakeError("409 conflict", status_code=409)
        self.calls.append(("update", kind, localization_id, dict(attributes)))

    def writes(self):
        return [c for c in self.calls if c[0] in ("create", "update")]


def iap(resource_id, product_id, name="Reference"):
    return {"id": resource_id, "attributes": {"productId": product_id, "name": name}}


def group(resource_id, reference_name):
    return {"id": resource_id, "attributes": {"referenceName": reference_name}}


class ReadPurchasesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_reads_each_kind_and_strips_the_trailing_newline(self):
        write(self.root, f"in_app_purchases/{LIFETIME}/en-US/name.txt", "Lifetime Premium\n")
        write(self.root, f"in_app_purchases/{LIFETIME}/en-US/description.txt", "Unlock everything\r\n")
        write(self.root, f"subscriptions/{MONTHLY}/es-ES/name.txt", "Premium mensual\n")
        write(self.root, f"subscription_groups/{GROUP}/en-US/name.txt", "Premium\n")
        write(self.root, f"subscription_groups/{GROUP}/en-US/custom_app_name.txt", "My App\n")
        local = pur.read_purchases(self.root)
        self.assertEqual(local.items[("in_app_purchase", LIFETIME)],
                         {"en-US": {"name": "Lifetime Premium", "description": "Unlock everything"}})
        self.assertEqual(local.items[("subscription", MONTHLY)], {"es-ES": {"name": "Premium mensual"}})
        self.assertEqual(local.items[("subscription_group", GROUP)],
                         {"en-US": {"name": "Premium", "custom_app_name": "My App"}})
        self.assertEqual(local.localization_count, 3)

    def test_a_missing_folder_is_not_an_error(self):
        write(self.root, f"subscriptions/{MONTHLY}/en-US/name.txt", "Monthly")
        local = pur.read_purchases(self.root)
        self.assertEqual(list(local.items), [("subscription", MONTHLY)])

    def test_ignores_empty_unknown_and_non_locale_entries_with_a_note(self):
        write(self.root, f"in_app_purchases/{LIFETIME}/en-US/name.txt", "Real")
        write(self.root, f"in_app_purchases/{LIFETIME}/en-US/description.txt", "  \n")
        write(self.root, f"in_app_purchases/{LIFETIME}/en-US/custom_app_name.txt", "x")
        write(self.root, f"in_app_purchases/{LIFETIME}/screenshots/name.txt", "x")
        local = pur.read_purchases(self.root)
        self.assertEqual(local.items[("in_app_purchase", LIFETIME)], {"en-US": {"name": "Real"}})
        joined = "\n".join(local.notes)
        self.assertIn("en-US/description.txt: empty", joined)
        self.assertIn("custom_app_name.txt: not a in-app purchase field", joined)
        self.assertIn("screenshots: not a locale code", joined)

    def test_filters_by_locale_product_and_skip_locales(self):
        for product in (LIFETIME, MONTHLY):
            for locale in ("en-US", "es-ES", "gl"):
                write(self.root, f"in_app_purchases/{product}/{locale}/name.txt", locale)
        local = pur.read_purchases(self.root, only_locales={"es-ES", "gl"}, skip_locales={"gl"},
                                   only_products={LIFETIME})
        self.assertEqual(list(local.items), [("in_app_purchase", LIFETIME)])
        self.assertEqual(list(local.items[("in_app_purchase", LIFETIME)]), ["es-ES"])
        self.assertTrue(any("gl" in n and "skip_locales" in n for n in local.notes))

    def test_not_a_directory(self):
        with self.assertRaises(FileNotFoundError):
            pur.read_purchases(self.root / "nope")

    def test_the_folders_are_the_ones_push_metadata_leaves_alone(self):
        self.assertEqual({k.folder for k in pur.KINDS}, set(md.PURCHASE_FOLDERS))
        write(self.root, "en-US/name.txt", "App")
        write(self.root, f"in_app_purchases/{LIFETIME}/en-US/name.txt", "x")
        write(self.root, f"subscriptions/{MONTHLY}/en-US/name.txt", "x")
        write(self.root, f"subscription_groups/{GROUP}/en-US/name.txt", "x")
        local = md.read_local(self.root)
        self.assertEqual(list(local.texts), ["en-US"])
        self.assertEqual(local.notes, [])


class LimitTests(unittest.TestCase):
    def items(self, kind, product, **fields):
        return {(kind, product): {"en-US": fields}}

    def test_a_purchase_name_over_30_and_a_description_over_45_are_rejected(self):
        problems = pur.check_limits(self.items("in_app_purchase", LIFETIME, name="x" * 31, description="y" * 46))
        self.assertEqual(len(problems), 2)
        self.assertIn("in_app_purchases/" + LIFETIME + "/en-US/name.txt: 31 characters, the limit is 30", problems[0])
        self.assertIn("description.txt: 46 characters, the limit is 45", problems[1])

    def test_texts_at_the_limit_pass(self):
        self.assertEqual(pur.check_limits(self.items("subscription", MONTHLY, name="x" * 30, description="y" * 45)), [])

    def test_group_names_have_no_local_limit(self):
        self.assertEqual(pur.check_limits(self.items("subscription_group", GROUP, name="x" * 80)), [])


class FetchRemoteTests(unittest.TestCase):
    def test_only_the_kinds_with_local_files_are_read(self):
        client = FakeClient(subs=[iap("s1", MONTHLY)], groups=[group("g1", GROUP)])
        remote = pur.fetch_remote(client, "42", {("subscription", MONTHLY)})
        self.assertEqual(list(remote), [("subscription", MONTHLY)])
        self.assertEqual(client.calls, [("list_subs",)])

    def test_products_match_by_product_id_and_groups_by_reference_name(self):
        client = FakeClient(
            iaps=[iap("p1", LIFETIME, name="Lifetime"), iap("p2", "other")],
            groups=[group("g1", GROUP), group("g2", "Other")],
            locs={("in_app_purchase", "p1"): {"en-US": loc("en-US", name="Old")}},
        )
        wanted = {("in_app_purchase", LIFETIME), ("subscription_group", GROUP), ("in_app_purchase", "missing")}
        remote = pur.fetch_remote(client, "42", wanted)
        self.assertEqual(sorted(remote), [("in_app_purchase", LIFETIME), ("subscription_group", GROUP)])
        self.assertEqual(remote[("in_app_purchase", LIFETIME)].id, "p1")
        self.assertIn("en-US", remote[("in_app_purchase", LIFETIME)].localizations)
        self.assertEqual(remote[("subscription_group", GROUP)].id, "g1")


class BuildPlanTests(unittest.TestCase):
    def remote(self, **localizations):
        return {("in_app_purchase", LIFETIME): pur.RemoteItem("p1", localizations)}

    def local(self, **locales):
        return {("in_app_purchase", LIFETIME): locales}

    def test_matching_text_is_unchanged_and_only_differences_are_listed(self):
        remote = self.remote(**{"en-US": loc("en-US", name="Lifetime Premium", description="Old")})
        plan = pur.build_plan(self.local(**{"en-US": {"name": "Lifetime Premium", "description": "New"}}), remote)
        self.assertEqual(plan.unchanged, 1)
        self.assertEqual([(c.field.file, c.old, c.new, c.new_locale) for c in plan.changes],
                         [("description", "Old", "New", False)])

    def test_a_trailing_newline_on_the_remote_side_does_not_count_as_a_difference(self):
        remote = self.remote(**{"en-US": loc("en-US", name="Lifetime Premium\n")})
        plan = pur.build_plan(self.local(**{"en-US": {"name": "Lifetime Premium"}}), remote)
        self.assertEqual((plan.changes, plan.unchanged), ([], 1))

    def test_a_new_locale_is_created_with_everything_given(self):
        plan = pur.build_plan(self.local(**{"de-DE": {"name": "Premium", "description": "Alles"}}), self.remote())
        self.assertEqual([c.new_locale for c in plan.changes], [True, True])
        self.assertEqual(plan.problems, [])
        self.assertEqual(plan.warnings, [])

    def test_a_new_locale_without_a_name_is_a_problem(self):
        plan = pur.build_plan(self.local(**{"de-DE": {"description": "Alles"}}), self.remote())
        self.assertEqual(plan.changes, [])
        self.assertIn("name.txt is required", plan.problems[0])

    def test_a_new_locale_without_a_description_warns_but_proceeds(self):
        plan = pur.build_plan(self.local(**{"de-DE": {"name": "Premium"}}), self.remote())
        self.assertEqual(len(plan.changes), 1)
        self.assertIn("no description.txt", plan.warnings[0])

    def test_a_group_does_not_need_a_description(self):
        items = {("subscription_group", GROUP): {"de-DE": {"name": "Premium"}}}
        remote = {("subscription_group", GROUP): pur.RemoteItem("g1", {})}
        plan = pur.build_plan(items, remote)
        self.assertEqual((len(plan.changes), plan.warnings), (1, []))

    def test_a_product_that_is_not_in_app_store_connect_is_a_problem(self):
        plan = pur.build_plan(self.local(**{"en-US": {"name": "x"}}), {})
        self.assertEqual(plan.changes, [])
        self.assertIn(f"In-app purchase '{LIFETIME}' is not in App Store Connect", plan.problems[0])

    def test_changes_to_a_localization_with_apple_are_flagged(self):
        remote = self.remote(**{
            "en-US": loc("en-US", state="APPROVED", name="Old"),
            "es-ES": loc("es-ES", name="Viejo"),
        })
        plan = pur.build_plan(self.local(**{"en-US": {"name": "New"}, "es-ES": {"name": "Nuevo"}}), remote)
        self.assertEqual([c.locale for c in pur.locked_changes(plan)], ["en-US"])

    def test_describe_change(self):
        change = pur.Change(pur.IN_APP_PURCHASE, LIFETIME, "en-US", pur.IN_APP_PURCHASE.fields[0], None, "New", True, None)
        self.assertEqual(pur.describe_change(change), '(empty) -> "New"')
        change = pur.Change(pur.IN_APP_PURCHASE, LIFETIME, "en-US", pur.IN_APP_PURCHASE.fields[0], "Old", "x" * 120, False, None)
        self.assertEqual(pur.describe_change(change), "3 -> 120 characters")


class PushScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / "asc-apps.json").write_text('{"app": {"app_id": "42", "metadata_path": "meta"}}')

    def text(self, relative, text):
        write(self.root / "meta", relative, text + "\n")

    def run_script(self, client, *extra):
        module = types.SimpleNamespace(AppStoreConnectClient=lambda: client, AppStoreConnectError=FakeError)
        argv = ["push_purchases.py", "--apps-file", str(self.root / "asc-apps.json"), "--app", "app", *extra]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(push_purchases, "load_client_module", return_value=module), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = push_purchases.main()
        return code, out.getvalue(), err.getvalue()

    def client(self):
        return FakeClient(
            iaps=[iap("p1", LIFETIME)],
            subs=[iap("s1", MONTHLY)],
            groups=[group("g1", GROUP)],
            locs={("in_app_purchase", "p1"): {"en-US": loc("en-US", name="Lifetime Premium", description="Old")}},
        )

    def test_dry_run_prints_the_comparison_and_writes_nothing(self):
        self.text(f"in_app_purchases/{LIFETIME}/en-US/name.txt", "Lifetime Premium")
        self.text(f"in_app_purchases/{LIFETIME}/en-US/description.txt", "Unlock all premium challenges")
        self.text(f"in_app_purchases/{LIFETIME}/de-DE/name.txt", "Premium auf Lebenszeit")
        client = self.client()
        code, out, err = self.run_script(client, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(client.writes(), [])
        self.assertIn(f"In-app purchase {LIFETIME}", out)
        self.assertIn('"Old" -> "Unlock all premium challenges"', out)
        self.assertIn("[new locale]", out)
        self.assertIn("Unchanged: 1 field(s).", out)
        self.assertIn("Dry run -- no changes made.", out)
        self.assertIn("no description.txt", out)

    def test_apply_updates_only_what_differs_and_creates_new_locales(self):
        self.text(f"in_app_purchases/{LIFETIME}/en-US/name.txt", "Lifetime Premium")
        self.text(f"in_app_purchases/{LIFETIME}/en-US/description.txt", "Unlock all premium challenges")
        self.text(f"in_app_purchases/{LIFETIME}/de-DE/name.txt", "Premium auf Lebenszeit")
        self.text(f"in_app_purchases/{LIFETIME}/de-DE/description.txt", "Alle Premium-Challenges")
        self.text(f"subscriptions/{MONTHLY}/en-US/name.txt", "Monthly Premium")
        self.text(f"subscription_groups/{GROUP}/en-US/name.txt", "Wakecraft Premium")
        client = self.client()
        code, out, err = self.run_script(client, "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(client.writes(), [
            ("create", "in_app_purchase", "p1", "de-DE",
             {"name": "Premium auf Lebenszeit", "description": "Alle Premium-Challenges"}),
            ("update", "in_app_purchase", "loc-en-US", {"description": "Unlock all premium challenges"}),
            ("create", "subscription", "s1", "en-US", {"name": "Monthly Premium"}),
            ("create", "subscription_group", "g1", "en-US", {"name": "Wakecraft Premium"}),
        ])
        self.assertIn("wrote description", out)

    def test_a_confirmation_that_is_declined_writes_nothing(self):
        self.text(f"subscriptions/{MONTHLY}/en-US/name.txt", "Monthly Premium")
        client = self.client()
        with mock.patch("builtins.input", return_value="n"):
            code, out, err = self.run_script(client)
        self.assertEqual(code, 1)
        self.assertEqual(client.writes(), [])

    def test_everything_already_matching_is_a_clean_no_op(self):
        self.text(f"in_app_purchases/{LIFETIME}/en-US/name.txt", "Lifetime Premium")
        client = self.client()
        code, out, err = self.run_script(client, "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(client.writes(), [])
        self.assertIn("No changes", out)

    def test_a_product_missing_remotely_stops_everything(self):
        self.text(f"in_app_purchases/{LIFETIME}/en-US/name.txt", "Changed")
        self.text(f"subscriptions/{MONTHLY}/en-US/name.txt", "Monthly Premium")
        client = FakeClient(iaps=[iap("p1", LIFETIME)], locs={("in_app_purchase", "p1"): {}})
        code, out, err = self.run_script(client, "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(client.writes(), [])
        self.assertIn(f"Subscription '{MONTHLY}' is not in App Store Connect", err)

    def test_text_over_the_limit_is_rejected_before_any_call(self):
        self.text(f"in_app_purchases/{LIFETIME}/en-US/name.txt", "x" * 31)
        client = self.client()
        code, out, err = self.run_script(client, "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(client.calls, [])
        self.assertIn("31 characters, the limit is 30", err)

    def test_products_and_locales_filters(self):
        for product, kind in ((LIFETIME, "in_app_purchases"), (MONTHLY, "subscriptions")):
            self.text(f"{kind}/{product}/en-US/name.txt", "English")
            self.text(f"{kind}/{product}/es-ES/name.txt", "Español")
        client = self.client()
        code, out, err = self.run_script(client, "--yes", "--products", MONTHLY, "--locales", "es-ES")
        self.assertEqual(code, 0, err)
        self.assertEqual(client.writes(), [("create", "subscription", "s1", "es-ES", {"name": "Español"})])

    def test_no_purchase_files_is_an_error(self):
        (self.root / "meta").mkdir()
        client = self.client()
        code, out, err = self.run_script(client)
        self.assertEqual(code, 1)
        self.assertIn("No purchase files found", err)

    def test_an_api_error_is_reported_and_stops(self):
        self.text(f"subscriptions/{MONTHLY}/en-US/name.txt", "Monthly Premium")
        client = self.client()
        client.fail_on_write = True
        code, out, err = self.run_script(client, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("409 conflict", err)
        self.assertIn("already written", err)


class AuditTests(unittest.TestCase):
    def test_subscriptions_and_groups_are_audited_next_to_in_app_purchases(self):
        class AuditClient(FakeClient):
            def list_versions(self, app_id, limit=50):
                return [{"id": "v1", "attributes": {"versionString": "1.0.0"}}]

            def list_localizations(self, version_id):
                return {"en-US": loc("en-US", description="d", keywords="k"),
                        "es-ES": loc("es-ES", description="d", keywords="k")}

            def find_editable_app_info(self, app_id):
                return None

            def list_app_infos(self, app_id):
                return []

            def find_game_center_detail_id(self, app_id):
                return None

            def list_iap_localizations(self, iap_id):
                return {"en-US": loc("en-US")}

        client = AuditClient(
            iaps=[iap("p1", LIFETIME, name="Lifetime")],
            subs=[iap("s1", MONTHLY, name="Monthly")],
            groups=[group("g1", GROUP)],
            locs={
                ("subscription", "s1"): {"en-US": loc("en-US"), "es-ES": loc("es-ES")},
                ("subscription_group", "g1"): {"en-US": loc("en-US")},
            },
        )
        app = types.SimpleNamespace(app_id="42", key="app", skip_locales=frozenset())
        report, gaps = audit_localizations.audit_app(client, app, None, None)
        self.assertRegex(report, r"GAP\s+In-app purchase: Lifetime\s+1/2\s+missing: es-ES")
        self.assertRegex(report, r"ok\s+Subscription: Monthly\s+2/2")
        self.assertRegex(report, r"GAP\s+Subscription group: Premium\s+1/2\s+missing: es-ES")
        self.assertEqual(gaps, 2)


@unittest.skipUnless(HAVE_CLIENT, "requests / PyJWT not installed")
class ClientPayloadTests(unittest.TestCase):
    def make_client(self, responses=None):
        client = object.__new__(asc_client.AppStoreConnectClient)
        client.calls = []

        def fake_request(method, path, **kwargs):
            client.calls.append((method, path, kwargs))
            return (responses or {}).get((method, path), {})

        client._request = fake_request
        return client

    def test_create_payloads_match_the_openapi_spec(self):
        expected = {
            "in_app_purchase": ("inAppPurchaseLocalizations", "inAppPurchaseV2", "inAppPurchases"),
            "subscription": ("subscriptionLocalizations", "subscription", "subscriptions"),
            "subscription_group": ("subscriptionGroupLocalizations", "subscriptionGroup", "subscriptionGroups"),
        }
        for kind, (resource, relationship, parent_type) in expected.items():
            client = self.make_client()
            client.create_purchase_localization(kind, "parent-1", "fr-FR", {"name": "Nom"})
            method, path, kwargs = client.calls[0]
            self.assertEqual((method, path), ("POST", f"/{resource}"))
            data = kwargs["json"]["data"]
            self.assertEqual(data["type"], resource)
            self.assertEqual(data["attributes"], {"name": "Nom", "locale": "fr-FR"})
            self.assertEqual(data["relationships"], {relationship: {"data": {"type": parent_type, "id": "parent-1"}}})

    def test_update_payload_carries_only_the_given_attributes(self):
        client = self.make_client()
        client.update_purchase_localization("subscription", "loc-9", {"description": "New"})
        method, path, kwargs = client.calls[0]
        self.assertEqual((method, path), ("PATCH", "/subscriptionLocalizations/loc-9"))
        self.assertEqual(kwargs["json"]["data"], {
            "type": "subscriptionLocalizations", "id": "loc-9", "attributes": {"description": "New"},
        })

    def test_list_purchase_localizations_paths_and_locale_keys(self):
        data = {"data": [{"id": "a", "attributes": {"locale": "en-US"}}, {"id": "b", "attributes": {"locale": "es-ES"}}]}
        client = self.make_client({
            ("GET", "/subscriptions/s1/subscriptionLocalizations"): data,
            ("GET", "/subscriptionGroups/g1/subscriptionGroupLocalizations"): data,
        })
        self.assertEqual(sorted(client.list_purchase_localizations("subscription", "s1")), ["en-US", "es-ES"])
        self.assertEqual(sorted(client.list_purchase_localizations("subscription_group", "g1")), ["en-US", "es-ES"])
        self.assertEqual([c[1] for c in client.calls], [
            "/subscriptions/s1/subscriptionLocalizations",
            "/subscriptionGroups/g1/subscriptionGroupLocalizations",
        ])

    def test_in_app_purchases_are_listed_through_the_v2_family(self):
        client = self.make_client()
        client.list_purchase_localizations("in_app_purchase", "p1")
        self.assertIn("/v2/inAppPurchases/p1/inAppPurchaseLocalizations", client.calls[0][1])

    def test_list_subscriptions_still_walks_the_groups(self):
        client = self.make_client({
            ("GET", "/apps/42/subscriptionGroups"): {"data": [{"id": "g1", "attributes": {}}]},
            ("GET", "/subscriptionGroups/g1/subscriptions"): {"data": [{"id": "s1", "attributes": {}}]},
        })
        self.assertEqual([s["id"] for s in client.list_subscriptions("42")], ["s1"])


if __name__ == "__main__":
    unittest.main()
