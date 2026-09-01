# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""
Security regression tests for the pieces that guard this app's trust boundaries.

Every case here corresponds to a real defect or a real guard, not to coverage
for its own sake:

  - `_verify_install_signature` is the ONLY thing standing between an anonymous
    caller and the customer's full staff roster (email + full name), because
    `list_crm_users` is `allow_guest=True`.
  - `_is_safe_oauth_authorize_url` and `_safe_return_to` are open-redirect
    guards on endpoints reachable by cross-site browser navigation.
  - `normalize_conduit_base_url` is what stops this site from posting its OAuth
    client secret to a plaintext or attacker-controlled origin.
  - the `pair_error` allowlist is the fix for a reflected XSS that executed with
    System Manager privileges.

Run: `bench --site <site> run-tests --app raremachines`
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe

from raremachines.api.connect import (
	ALLOWED_ERROR_CODES,
	_is_safe_oauth_authorize_url,
	_safe_return_to,
)
from raremachines.api.org_users import SIGNATURE_MAX_SKEW_SECONDS
from raremachines.config.saas import (
	DEFAULT_CONDUIT_BASE_URL,
	get_conduit_base_url,
	normalize_conduit_base_url,
)

SECRET = "s" * 64


def _sign(secret: str, timestamp: str, body: bytes) -> str:
	return hmac.new(
		secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256
	).hexdigest()


class TestInstallSignature(unittest.TestCase):
	"""The HMAC scheme protecting `list_crm_users`."""

	def test_valid_signature_matches(self):
		ts = str(int(time.time()))
		body = b"{}"
		self.assertEqual(_sign(SECRET, ts, body), _sign(SECRET, ts, body))

	def test_signature_is_bound_to_the_timestamp(self):
		"""Signing the body alone made every request replayable forever.

		The timestamp is inside the HMAC precisely so it cannot be edited to
		refresh a captured signature.
		"""
		body = b"{}"
		self.assertNotEqual(_sign(SECRET, "1000", body), _sign(SECRET, "2000", body))

	def test_signature_is_bound_to_the_body(self):
		ts = str(int(time.time()))
		self.assertNotEqual(_sign(SECRET, ts, b"{}"), _sign(SECRET, ts, b'{"x":1}'))

	def test_wrong_secret_does_not_verify(self):
		ts = str(int(time.time()))
		self.assertNotEqual(_sign(SECRET, ts, b"{}"), _sign("w" * 64, ts, b"{}"))

	def test_skew_window_is_bounded_and_short(self):
		"""A wide window is a long replay window. Five minutes absorbs clock
		drift between two hosts without being useful to an attacker."""
		self.assertGreater(SIGNATURE_MAX_SKEW_SECONDS, 0)
		self.assertLessEqual(SIGNATURE_MAX_SKEW_SECONDS, 600)


class TestOAuthAuthorizeUrlGuard(unittest.TestCase):
	"""`oauth_relogin` redirects a browser to this URL — it must stay on-site."""

	def test_accepts_this_sites_authorize_endpoint(self):
		site = frappe.utils.get_url().rstrip("/")
		self.assertTrue(
			_is_safe_oauth_authorize_url(f"{site}/api/method/frappe.integrations.oauth2.authorize")
		)

	def test_accepts_with_query_string(self):
		site = frappe.utils.get_url().rstrip("/")
		url = f"{site}/api/method/frappe.integrations.oauth2.authorize?client_id=x&response_type=code"
		self.assertTrue(_is_safe_oauth_authorize_url(url))

	def test_rejects_a_foreign_host(self):
		self.assertFalse(
			_is_safe_oauth_authorize_url(
				"https://evil.example/api/method/frappe.integrations.oauth2.authorize"
			)
		)

	def test_rejects_a_non_authorize_path_on_this_site(self):
		site = frappe.utils.get_url().rstrip("/")
		self.assertFalse(_is_safe_oauth_authorize_url(f"{site}/api/method/frappe.handler.logout"))

	def test_rejects_non_http_schemes(self):
		for url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x"):
			self.assertFalse(_is_safe_oauth_authorize_url(url))

	def test_rejects_empty_and_garbage(self):
		for url in ("", "   ", "not a url"):
			self.assertFalse(_is_safe_oauth_authorize_url(url))


class TestSafeReturnTo(unittest.TestCase):
	"""`finish_from_web` sends the browser here after pairing."""

	def test_rejects_empty(self):
		self.assertIsNone(_safe_return_to(None))
		self.assertIsNone(_safe_return_to(""))

	def test_rejects_a_foreign_origin(self):
		self.assertIsNone(_safe_return_to("https://evil.example/onboarding/frappe"))

	def test_rejects_non_string(self):
		self.assertIsNone(_safe_return_to(12345))  # type: ignore[arg-type]


class TestRareMachineBaseUrl(unittest.TestCase):
	"""This URL receives the OAuth client secret at pair time."""

	def test_accepts_https(self):
		self.assertEqual(normalize_conduit_base_url("https://whitesense.in"), "https://whitesense.in")

	def test_strips_trailing_slash(self):
		self.assertEqual(normalize_conduit_base_url("https://whitesense.in/"), "https://whitesense.in")

	def test_allows_localhost_over_http_for_development_only(self):
		self.assertEqual(normalize_conduit_base_url("http://localhost:3000"), "http://localhost:3000")
		self.assertEqual(normalize_conduit_base_url("http://127.0.0.1:3000"), "http://127.0.0.1:3000")

	def test_rejects_plaintext_http_to_a_public_host(self):
		"""The client secret must never cross a plaintext hop."""
		with self.assertRaises(frappe.ValidationError):
			normalize_conduit_base_url("http://evil.example")

	def test_rejects_empty_and_schemeless(self):
		for raw in ("", "   ", "whitesense.in", "ftp://whitesense.in"):
			with self.assertRaises(frappe.ValidationError):
				normalize_conduit_base_url(raw)


class TestBaseUrlResolution(unittest.TestCase):
	"""Which RareMachine this site talks to, and who gets to decide.

	RareMachine is a hosted service. A customer install must reach it with no
	configuration at all — and must NOT be diverted by a flag the customer set
	for their own unrelated reasons.
	"""

	@staticmethod
	def _resolve(conf: dict) -> str:
		with patch.object(frappe, "conf", frappe._dict(conf)):
			return get_conduit_base_url()

	def test_plain_site_reaches_the_hosted_service(self):
		self.assertEqual(self._resolve({}), DEFAULT_CONDUIT_BASE_URL)

	def test_developer_mode_alone_does_not_divert_the_site(self):
		"""`developer_mode` is the SITE OWNER's flag, not this app's.

		Self-hosted benches and staging sites routinely run with it on, and some
		production sites never turn it off. Treating it as "point at localhost"
		would send such a site to a port on its own server, and `install.py`
		persists the resolved value into RareMachine Settings, so it would stick.
		"""
		self.assertEqual(self._resolve({"developer_mode": 1}), DEFAULT_CONDUIT_BASE_URL)

	def test_explicit_override_wins(self):
		self.assertEqual(
			self._resolve({"raremachines_base_url": "https://app.example.com"}),
			"https://app.example.com",
		)

	def test_override_still_wins_when_developer_mode_is_on(self):
		"""The override is the ONLY way to point elsewhere — including locally."""
		self.assertEqual(
			self._resolve({"developer_mode": 1, "raremachines_base_url": "http://localhost:3000"}),
			"http://localhost:3000",
		)

	def test_pre_rename_key_is_still_honoured(self):
		"""A site configured before the rename must keep working."""
		self.assertEqual(
			self._resolve({"conduit_base_url": "https://app.example.com"}),
			"https://app.example.com",
		)

	def test_a_hostile_override_is_still_normalised(self):
		"""The override is not a bypass of the transport rules."""
		with self.assertRaises(frappe.ValidationError):
			self._resolve({"raremachines_base_url": "http://evil.example"})


class TestPairErrorAllowlist(unittest.TestCase):
	"""Guard against reflected XSS in the RareMachine Settings form.

	`pair_error` is read from the URL and rendered by `frappe.msgprint`, which
	appends its message as raw HTML. The client must therefore look codes up in
	a fixed table and never interpolate the parameter. Asserted against the
	shipped JS so the guard cannot be quietly removed.
	"""

	def setUp(self):
		self.js = (
			Path(frappe.get_app_path("raremachines"))
			/ "raremachines"
			/ "doctype"
			/ "raremachine_settings"
			/ "raremachine_settings.js"
		).read_text(encoding="utf-8")

	def test_client_declares_a_lookup_table(self):
		self.assertIn("PAIR_ERROR_MESSAGES", self.js)

	def test_client_never_interpolates_the_raw_parameter(self):
		"""The original defect was `__("Error code: {0}", [pairErr])`."""
		self.assertNotRegex(self.js, r"__\(\s*[\"'][^\"']*\{0\}[^\"']*[\"']\s*,\s*\[\s*pairErr")
		# and it must not reach msgprint through any other interpolation
		self.assertNotRegex(self.js, r"message[^\n]*\+\s*pairErr")
		self.assertNotRegex(self.js, r"\$\{\s*pairErr\s*\}")

	def test_every_server_error_code_has_client_copy(self):
		"""A code the server can emit but the client cannot render would fall
		back to the generic string — acceptable, but better to notice."""
		table = re.search(r"PAIR_ERROR_MESSAGES\s*=\s*\{(.*?)\n\};", self.js, re.S)
		self.assertIsNotNone(table, "PAIR_ERROR_MESSAGES table not found")
		declared = set(re.findall(r"^\s*([A-Z_]+):", table.group(1), re.M))
		self.assertEqual(set(ALLOWED_ERROR_CODES) - declared, set())


class TestWhitelistedEndpointMethods(unittest.TestCase):
	"""State-changing endpoints must not be reachable by GET.

	Frappe only validates a CSRF token on non-GET requests, so a GET-able
	mutation is forgeable from any third-party page. Frappe's own `logout` is
	`methods=["POST"]` for exactly this reason.
	"""

	@staticmethod
	def _allowed_methods(fn):
		"""Frappe records this in a module-level dict keyed on the decorated
		function, not as an attribute on it (frappe/__init__.py `whitelist`).
		An absent `methods=` defaults to all four verbs, so a missing entry is
		itself the failure this test is looking for."""
		return frappe.allowed_http_methods_for_whitelisted_func.get(fn)

	def test_state_changing_endpoints_are_post_only(self):
		from raremachines.api import connect

		for name in ("get_pair_defaults", "begin_connect_with_conduit", "disconnect", "oauth_relogin_switch"):
			methods = self._allowed_methods(getattr(connect, name))
			self.assertEqual(methods, ["POST"], f"{name} must be POST-only, got {methods}")

	def test_oauth_relogin_get_is_declared_side_effect_free(self):
		"""It is reached by cross-site redirect so it cannot be POST-only; the
		guarantee is instead that GET changes nothing (the logout moved to
		`oauth_relogin_switch`)."""
		from raremachines.api import connect

		self.assertEqual(self._allowed_methods(connect.oauth_relogin), ["GET"])
		src = Path(frappe.get_app_path("raremachines"), "api", "connect.py").read_text(encoding="utf-8")
		body = src.split("def oauth_relogin(")[1].split("def oauth_relogin_switch(")[0]
		self.assertNotIn("login_manager.logout()", body)
		self.assertNotIn("clear_cookies()", body)


class TestUninstallCleanup(unittest.TestCase):
	"""Uninstall must not leave live credentials on the customer's site."""

	def test_uninstall_hook_is_registered(self):
		hooks = frappe.get_hooks("before_uninstall", app_name="raremachines")
		self.assertIn("raremachines.uninstall.before_uninstall", hooks)

	def test_uninstall_module_targets_every_leftover(self):
		src = Path(frappe.get_app_path("raremachines"), "uninstall.py").read_text(encoding="utf-8")
		for artifact in (
			"OAuth Bearer Token",
			"OAuth Authorization Code",
			"OAuth Client",
			"Workspace Shortcut",
		):
			self.assertIn(artifact, src, f"uninstall does not clean up {artifact}")


class TestSettingsDoctypePermissions(unittest.TestCase):
	"""`install_secret` lives here; only System Manager may read the doctype."""

	def test_install_secret_is_a_hidden_readonly_password_field(self):
		meta = frappe.get_meta("RareMachine Settings")
		field = meta.get_field("install_secret")
		self.assertIsNotNone(field)
		self.assertEqual(field.fieldtype, "Password")
		self.assertTrue(field.hidden)
		self.assertTrue(field.read_only)

	def test_only_system_manager_has_access(self):
		path = (
			Path(frappe.get_app_path("raremachines"))
			/ "raremachines"
			/ "doctype"
			/ "raremachine_settings"
			/ "raremachine_settings.json"
		)
		schema = json.loads(path.read_text(encoding="utf-8"))
		roles = {p.get("role") for p in schema.get("permissions", [])}
		self.assertEqual(roles, {"System Manager"})


class TestFinishFromWebRequiresConfirmation(unittest.TestCase):
	"""
	`finish_from_web` is `allow_guest` and accepts GET, and unlike
	`finish_browser_pair` it has NO site-bound nonce — the pair ticket is
	minted on RareMachine for whichever workspace asked for it. Frappe does
	not check CSRF on GET (auth.py SAFE_HTTP_METHODS), so without the
	confirmation gate a cross-site request loaded by a System Manager could
	bind this site's clientSecret and installSecret to a workspace the admin
	never chose, with replace=1 overwriting a legitimate pairing.
	"""

	def _source(self):
		import inspect

		import raremachines.api.connect as connect

		return inspect.getsource(connect.finish_from_web)

	def test_get_is_gated_before_the_pairing_call(self):
		src = self._source()
		gate = src.index('method == "GET"')
		pair = src.index("_complete_pair_with_ticket")
		self.assertLess(
			gate, pair, "the GET gate must come BEFORE the pairing call, or a forged GET still pairs"
		)

	def test_get_branch_returns_without_pairing(self):
		src = self._source()
		gate = src.index('method == "GET"')
		pair = src.index("_complete_pair_with_ticket")
		# The GET branch must terminate — a confirmation page then `return`.
		self.assertIn("respond_as_web_page", src[gate:pair])
		self.assertIn("return", src[gate:pair])

	def test_confirmation_form_posts_with_a_csrf_token(self):
		src = self._source()
		self.assertIn('method="POST"', src)
		self.assertIn("csrf_token", src)

	def test_still_accepts_post(self):
		import raremachines.api.connect as connect

		# Same lookup TestWhitelistedEndpointMethods uses — Frappe keeps this in
		# a module-level dict, not as an attribute on the function.
		methods = frappe.allowed_http_methods_for_whitelisted_func.get(connect.finish_from_web)
		self.assertIn("POST", methods)


class TestPairPayloadNotLeakedToErrorLog(unittest.TestCase):
	"""
	Frappe logs unhandled exceptions with frame LOCALS attached, and its
	sanitizer redacts dict entries only for keys EXACTLY matching
	password/passwd/secret/token/key/pwd. `clientSecret`/`installSecret` match
	none of those, so holding that dict across the response-parsing code wrote
	both secrets into the customer's Error Log in plaintext, permanently —
	contradicting README's "never ... written to a log".
	"""

	def _source(self):
		import inspect

		import raremachines.api.connect as connect

		return inspect.getsource(connect._complete_pair_with_ticket)

	def test_payload_is_deleted_before_response_parsing(self):
		src = self._source()
		self.assertIn("del payload", src)
		# And it must happen before the JSON handling that can raise.
		self.assertLess(src.index("del payload"), src.index("resp.status_code >= 400"))

	def test_non_dict_json_body_cannot_raise_attributeerror(self):
		"""A CDN/WAF error page returning a top-level JSON array reached
		`.get()` on a list -> AttributeError, which `except ValueError` misses."""
		src = self._source()
		self.assertIn("isinstance(body, dict)", src)
		self.assertIn("isinstance(data, dict)", src)


class TestLoopbackAllowlistIsHostBased(unittest.TestCase):
	"""`startswith("http://127.0.0.1")` is also true of
	`http://127.0.0.1.evil.com`, which bypasses the https requirement and
	sends clientSecret/installSecret in plaintext to a remote host."""

	def test_rejects_lookalike_loopback_hosts(self):
		from raremachines.config.saas import (
			DEFAULT_CONDUIT_BASE_URL,
			get_conduit_base_url,
			normalize_conduit_base_url,
		)

		for bad in (
			"http://127.0.0.1.evil.com",
			"http://localhost.attacker.tld",
			"http://localhost.evil.co/path",
		):
			with self.assertRaises(Exception, msg=f"{bad} was accepted"):
				normalize_conduit_base_url(bad)

	def test_still_allows_real_loopback(self):
		from raremachines.config.saas import (
			DEFAULT_CONDUIT_BASE_URL,
			get_conduit_base_url,
			normalize_conduit_base_url,
		)

		self.assertEqual(normalize_conduit_base_url("http://localhost:3000"), "http://localhost:3000")
		self.assertEqual(normalize_conduit_base_url("http://127.0.0.1:3000"), "http://127.0.0.1:3000")


class TestSignatureTimestampHardening(unittest.TestCase):
	def _source(self):
		import inspect

		import raremachines.api.org_users as ou

		return inspect.getsource(ou._verify_install_signature)

	def test_nan_timestamp_is_rejected(self):
		# Every comparison against NaN is False, so `skew > MAX` would pass.
		self.assertIn("isfinite", self._source())

	def test_non_ascii_signature_is_rejected_before_compare_digest(self):
		# compare_digest raises TypeError on a non-ASCII str (werkzeug decodes
		# headers latin-1), and the traceback records `expected` — the CORRECT
		# HMAC — into the Error Log.
		self.assertIn("isascii", self._source())

	def test_list_crm_users_is_post_only(self):
		import raremachines.api.org_users as ou

		methods = frappe.allowed_http_methods_for_whitelisted_func.get(ou.list_crm_users)
		self.assertEqual(methods, ["POST"], f"expected POST-only, got {methods}")


class TestInstallIdentityPersistedBeforePairing(unittest.TestCase):
	"""
	RareMachine calls back into `verify_install` DURING the pairing POST, to
	prove this site is reachable at the URL being claimed. That callback is a
	separate request with its own DB connection, so the install identity must
	be COMMITTED before the POST — otherwise the site fails its own ownership
	challenge and pairing dies with SITE_VERIFICATION_FAILED.

	`_ensure_install_identity` only sets the fields in memory, so the commit
	must not be deferred until after the pairing POST returns.
	"""

	def test_identity_is_committed_before_the_outbound_post(self):
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect._complete_pair_with_ticket)
		identity = src.index("_ensure_install_identity")
		commit = src.index("frappe.db.commit()", identity)
		post = src.index("session.post(")
		self.assertLess(
			commit,
			post,
			"install identity must be committed BEFORE the pairing POST — the "
			"verify_install callback reads it from the database",
		)


class TestWhatsAppRelayEndpoints(unittest.TestCase):
	"""`receive_whatsapp_lead` / `receive_whatsapp_status` — RareMachine's two
	new server-to-server calls into this site (see connect.py's "RareMachine
	→ Frappe WhatsApp relay" section). Same structural-declaration and
	source-inspection style as `TestWhitelistedEndpointMethods` /
	`TestSignatureTimestampHardening` above — live DB round trips for these
	are covered by manual verification against the local bench (same
	discipline used for `whatsapp_lead.py`'s auto-lead-creation feature),
	not by this offline suite.
	"""

	def test_both_endpoints_are_guest_post_only(self):
		import raremachines.api.connect as connect

		for name in (
			"receive_whatsapp_lead",
			"receive_whatsapp_status",
			"receive_brochure_choice",
			"list_export_certificates",
			"get_brochure_pdf_url",
			"get_company_profile_pdf_url",
		):
			fn = getattr(connect, name)
			methods = frappe.allowed_http_methods_for_whitelisted_func.get(fn)
			self.assertEqual(methods, ["POST"], f"{name} must be POST-only, got {methods}")

	def test_both_endpoints_verify_the_install_signature_first(self):
		"""Same fail-closed discipline as `list_crm_users`/`verify_install` —
		the very first statement in the function body must be the signature
		check, not a DB read of untrusted input."""
		import inspect

		import raremachines.api.connect as connect

		for name in (
			"receive_whatsapp_lead",
			"receive_whatsapp_status",
			"receive_brochure_choice",
			"list_export_certificates",
			"get_brochure_pdf_url",
			"get_company_profile_pdf_url",
		):
			src = inspect.getsource(getattr(connect, name))
			body = src.split('"""', 2)[-1] if '"""' in src else src
			self.assertIn("_verify_install_signature()", body)
			# The very first executable line after the docstring, not buried
			# after any DB access.
			first_stmt = next(
				line.strip()
				for line in body.splitlines()
				if line.strip() and not line.strip().startswith("#")
			)
			self.assertEqual(first_stmt, "_verify_install_signature()")

	def test_receive_whatsapp_status_does_not_call_update_message_status(self):
		"""`update_message_status` raises `DoesNotExistError` on no match,
		which is the EXPECTED case here (most forwarded status events are for
		messages RareMachine itself sent, unknown to this site). Regression
		guard against accidentally routing through the raising function —
		checks the executable body only, since the function's own docstring
		names `update_message_status` in prose to explain why it is NOT
		called."""
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect.receive_whatsapp_status)
		body = src.split('"""', 2)[-1]
		self.assertNotIn("update_message_status", body)

	def test_receive_whatsapp_lead_is_idempotent_by_message_id(self):
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect.receive_whatsapp_lead)
		self.assertIn('frappe.db.exists("WhatsApp Message", {"message_id": message_id})', src)

	def test_receive_whatsapp_lead_treats_a_concurrent_duplicate_insert_as_a_no_op(self):
		"""Found in review (2026-08-31): the `exists()` check above is only a
		fast path — under a genuinely concurrent retry, two requests can both
		pass it before either inserts. The real guarantee is the DB-level
		unique constraint on `message_id`
		(`install.py::_ensure_whatsapp_message_id_unique`); the insert itself
		must be wrapped so a `UniqueValidationError` from that constraint is
		caught and treated as a benign no-op, not an unhandled exception."""
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect.receive_whatsapp_lead)
		self.assertIn("except frappe.UniqueValidationError:", src)

	def test_whatsapp_message_id_unique_constraint_is_registered_on_install(self):
		import inspect

		import raremachines.install as install

		src = inspect.getsource(install._ensure_whatsapp_message_id_unique)
		self.assertIn('make_property_setter("WhatsApp Message", "message_id", "unique", "1", "Check")', src)
		for hook in ("after_install", "after_migrate"):
			hook_src = inspect.getsource(getattr(install, hook))
			self.assertIn("_ensure_whatsapp_message_id_unique()", hook_src)

	def test_connect_py_comments_never_name_rare_machines_own_internal_files(self):
		"""Found in review (2026-08-31): this repo is public. Comments here
		must describe the required wire behaviour a caller needs, never leak
		RareMachine's own closed-source implementation details (file paths,
		function names, its job-queue library) into a public file."""
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect)
		for leaked_detail in (
			"apps/web/src/lib",
			"packages/connectors/frappe",
			"forwardWhatsAppLeadMessage",
			"forwardWhatsAppStatusEvent",
			"pg-boss",
		):
			self.assertNotIn(leaked_detail, src, f"leaked RareMachine-internal detail: {leaked_detail!r}")


class TestGuidedIntakeEndpoints(unittest.TestCase):
	"""`receive_brochure_choice` / `list_export_certificates` — the two
	endpoints the guided-intake plan (2026-08-26) added on top of the
	original relay. Same structural-check style as the class above; live
	round trips covered by manual verification against the local bench
	(idempotent lead-field application, brochure_pdf resolution from
	`RareMachine Settings`, bad-leadId rejection — all confirmed live)."""

	def test_receive_brochure_choice_rejects_an_unknown_lead(self):
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect.receive_brochure_choice)
		self.assertIn('frappe.db.exists("CRM Lead", lead_name)', src)

	def test_receive_brochure_choice_only_accepts_domestic_or_export(self):
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect.receive_brochure_choice)
		self.assertIn('market_type not in ("Domestic", "Export")', src)

	def test_brochure_pdf_is_resolved_from_settings_never_hardcoded(self):
		"""The 'global default brochure' must come from the admin-configured
		`RareMachine Settings` fields, never a literal path in this app."""
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect._resolve_brochure_pdf)
		self.assertIn('frappe.db.get_single_value("RareMachine Settings"', src)

	def test_company_profile_pdf_is_resolved_from_settings_never_hardcoded(self):
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect.get_company_profile_pdf_url)
		self.assertIn('frappe.db.get_single_value("RareMachine Settings", "company_profile_file")', src)

	def test_private_company_profile_file_is_not_returned_to_whatsapp(self):
		import raremachines.api.connect as connect

		self.assertIsNone(
			connect._absolute_public_file_url(
				"/private/files/profile.pdf",
				"test private company profile",
			)
		)

	def test_list_export_certificates_only_returns_enabled_rows(self):
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect.list_export_certificates)
		self.assertIn('filters={"enabled": 1}', src)

	def test_guided_intake_customizations_are_gated_behind_an_explicit_flag(self):
		"""`raremachines` is installed on every client's own site — Nest's
		Domestic/Export Lead fields must never be created by default for a
		future client who hasn't opted in."""
		import inspect

		import raremachines.install as install

		src = inspect.getsource(install._ensure_whatsapp_intake_customizations)
		self.assertIn('frappe.db.get_single_value("RareMachine Settings", "whatsapp_intake_enabled")', src)

	def test_settings_save_reapplies_guided_intake_customizations_when_enabled(self):
		"""Turning the toggle on after install must create the dependent
		custom fields without waiting for a lucky future migrate."""
		import inspect

		from raremachines.raremachines.doctype.raremachine_settings.raremachine_settings import (
			RareMachineSettings,
		)

		src = inspect.getsource(RareMachineSettings.on_update)
		self.assertIn("_ensure_whatsapp_intake_customizations", src)
		self.assertIn("whatsapp_intake_enabled", src)

	def test_nest_specific_seed_data_is_not_in_the_shared_install_hooks(self):
		"""Certificate names and brochure email copy are Nest Healthcare's own
		business content — they must live in `setup_nest_healthcare.py`
		(run once, by hand, against Nest's site only), never in
		`install.py`'s `after_install`/`after_migrate`, which run on every
		site that installs this shared app."""
		import inspect

		import raremachines.install as install

		src = inspect.getsource(install)
		# Checks the actual leak indicators (certificate names, seeded email
		# copy) — not a bare "Nest Healthcare" string match, which would also
		# flag the module's own legitimate explanatory prose about why this
		# gate exists.
		for leaked_string in ("WHO-GMP", "Cambodia", "Afghanistan", "brochure from Nest Healthcare"):
			self.assertNotIn(leaked_string, src, f"{leaked_string!r} must not appear in install.py")

	def test_guided_intake_fields_are_length_capped_before_a_lead_save(self):
		"""Found in review (2026-08-31): this endpoint is HMAC-authenticated,
		not open to the public internet, but nothing capped `name`/`company`/
		`email` before they hit `lead.save()` — an oversized value should be
		truncated up front rather than relying on the save to fail."""
		import inspect

		import raremachines.api.connect as connect

		src = inspect.getsource(connect._apply_guided_intake_fields)
		self.assertIn("_INTAKE_FIELD_MAX_LENGTH", src)


class TestWhatsAppLeadHooksAreGated(unittest.TestCase):
	"""`normalize_lead_mobile_no` / `ensure_lead_for_unmatched_sender` — both
	`doc_events` hooks on `CRM Lead`/`WhatsApp Message` respectively, fired
	for every site with this app installed. Found in review (2026-08-31):
	neither checked `RareMachine Settings.whatsapp_intake_enabled` before
	this fix, so a client who never opted into the guided WhatsApp intake
	feature still got their Leads auto-created and their phone numbers
	silently rewritten."""

	def test_normalize_lead_mobile_no_is_gated_behind_the_intake_toggle(self):
		import inspect

		import raremachines.api.whatsapp_lead as whatsapp_lead

		src = inspect.getsource(whatsapp_lead.normalize_lead_mobile_no)
		self.assertIn('frappe.db.get_single_value("RareMachine Settings", "whatsapp_intake_enabled")', src)

	def test_normalize_lead_mobile_no_never_assumes_india_as_a_fallback(self):
		"""An unset `System Settings.country` must mean 'don't guess', not
		'assume India' — a non-Indian client saving a legitimate bare
		10-digit number must never have it silently corrupted with the
		wrong country code."""
		import inspect

		import raremachines.api.whatsapp_lead as whatsapp_lead

		src = inspect.getsource(whatsapp_lead.normalize_lead_mobile_no)
		self.assertNotIn('or "India"', src)
		self.assertNotIn('"+91"', src)

	def test_ensure_lead_for_unmatched_sender_is_gated_behind_the_intake_toggle(self):
		import inspect

		import raremachines.api.whatsapp_lead as whatsapp_lead

		src = inspect.getsource(whatsapp_lead.ensure_lead_for_unmatched_sender)
		self.assertIn('frappe.db.get_single_value("RareMachine Settings", "whatsapp_intake_enabled")', src)
