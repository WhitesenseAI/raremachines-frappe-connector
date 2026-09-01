# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""
Pair this Frappe site with a RareMachine workspace via OAuth Client.

UX:
  - Frappe-first: begin_connect_with_conduit → RareMachine confirm → finish_browser_pair
  - Website-first: RareMachine site URL → finish_from_web (pair ticket in URL, no paste)
No paste-code desk flow. Pair tickets exist only browser handoff.

Secrecy: never log client_secret, pair tickets, install_secret, or HTTP bodies.
Log only lifecycle events + coarse error codes.
"""

from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import frappe
from frappe import _
from frappe.utils import get_request_session

from raremachines.config.saas import get_conduit_base_url, normalize_conduit_base_url

ALLOWED_ERROR_CODES = frozenset(
	{
		"PAIRING_EXPIRED",
		"UNAUTHORIZED",
		"CONFIG_INVALID",
		"SSRF_REJECTED",
		"CONNECT_FAILED",
		"NETWORK_UNREACHABLE",
		"SITE_MISMATCH",
		"SITE_ALREADY_LINKED",
		"SITE_VERIFICATION_FAILED",
		"KMS_MISCONFIGURED",
	}
)

LOGGER = frappe.logger("raremachines", allow_site=True, file_count=2)

CONDUIT_OAUTH_APP_NAME = "RareMachines"
CLAIM_TTL_SECONDS = 15 * 60
CLAIM_CACHE_PREFIX = "conduit_pair_claim:"


def _set_error(code: str) -> None:
	code = code if code in ALLOWED_ERROR_CODES else "CONNECT_FAILED"
	settings = frappe.get_single("RareMachine Settings")
	settings.connection_status = "Error"
	settings.last_error_code = code
	settings.last_error_at = frappe.utils.now_datetime()
	settings.save(ignore_permissions=True)
	# Manual commit: durability across a browser redirect — the user leaves this site immediately after, so an uncommitted pairing state would be rolled back and lost.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _crm_capable_roles() -> list[str]:
	"""Roles that can actually read a CRM Lead on THIS site.

	Derived from the site's own permission rules rather than a hardcoded list,
	because role names are customisable — a site with a bespoke "Revenue Rep"
	role must keep working, and a hardcoded list would either lock those users
	out or silently drift.

	Custom DocPerm shadows the shipped DocPerm when present (Frappe's own
	rule), so it is preferred and DocPerm is only consulted if the doctype has
	not been customised. System Manager is always included: it can grant itself
	any permission anyway, so excluding it would be theatre that also breaks
	the admin who performs the pairing.
	"""
	roles: set[str] = {"System Manager"}
	for doctype in ("Custom DocPerm", "DocPerm"):
		try:
			rows = frappe.get_all(
				doctype,
				filters={"parent": "CRM Lead", "permlevel": 0, "read": 1},
				pluck="role",
			)
		except Exception:
			rows = []
		if rows:
			roles.update(r for r in rows if r)
			break
	# "All" is every authenticated user including Website/portal accounts —
	# never a CRM-capable role, even if a permission rule names it.
	roles.discard("All")
	roles.discard("Guest")
	return sorted(roles)


def _ensure_oauth_client_allowed_roles(doc) -> None:
	"""Restrict who may complete personal OAuth to CRM-capable roles.

	The client's allowed-roles list decides who can complete a personal
	grant, and it must never contain "All". That role covers every
	authenticated account on the site — Website/portal users, customer
	self-signups, contractors. Combined with RareMachine's `join/start`
	flow, which admits anyone who can authenticate here and whose email
	matches, "All" would make a portal account on this site a member of the
	customer's RareMachine workspace with no admin approval.

	"Can authenticate here" is the wrong gate; "is a CRM user here" is the
	right one, and this site already knows the answer. It is the same bar
	`api/org_users.list_crm_users` applies: enabled System Users who can
	read CRM Lead.

	Roles outside that set are PRUNED, not merely skipped, and this runs on
	every save rather than only at creation. A rule enforced for new clients
	alone would leave an already-configured site broader than intended.
	"""
	allowed = _crm_capable_roles()
	kept = [d for d in (doc.allowed_roles or []) if d.role in allowed]
	dropped = [d.role for d in (doc.allowed_roles or []) if d.role not in allowed]
	if dropped:
		LOGGER.info("oauth_client_roles_pruned roles=%s", ",".join(sorted(set(dropped))))
	doc.allowed_roles = kept
	present = {d.role for d in kept}
	for role in allowed:
		if role not in present:
			doc.append("allowed_roles", {"role": role})


def _ensure_oauth_client(redirect_uri: str) -> tuple[str, str]:
	"""Create or update OAuth Client for RareMachine. Returns (client_id, client_secret)."""
	existing = frappe.db.get_value("OAuth Client", {"app_name": CONDUIT_OAUTH_APP_NAME}, "name")
	if existing:
		doc = frappe.get_doc("OAuth Client", existing)
		uris = (doc.redirect_uris or "").split()
		if redirect_uri not in uris:
			uris.append(redirect_uri)
		doc.redirect_uris = " ".join(uris)
		doc.default_redirect_uri = redirect_uri
		doc.grant_type = "Authorization Code"
		doc.response_type = "Code"
		doc.scopes = "all openid"
		doc.skip_authorization = 0
		_ensure_oauth_client_allowed_roles(doc)
		doc.save(ignore_permissions=True)
		# Manual commit: durability across a browser redirect — the user leaves this site immediately after, so an uncommitted pairing state would be rolled back and lost.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
		return doc.client_id or doc.name, doc.client_secret

	doc = frappe.get_doc(
		{
			"doctype": "OAuth Client",
			"app_name": CONDUIT_OAUTH_APP_NAME,
			"scopes": "all openid",
			"redirect_uris": redirect_uri,
			"default_redirect_uri": redirect_uri,
			"grant_type": "Authorization Code",
			"response_type": "Code",
			"skip_authorization": 0,
		}
	)
	_ensure_oauth_client_allowed_roles(doc)
	doc.insert(ignore_permissions=True)
	# Manual commit: durability across a browser redirect — the user leaves this site immediately after, so an uncommitted pairing state would be rolled back and lost.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	doc.reload()
	return doc.client_id or doc.name, doc.client_secret


def _claim_cache_key(nonce: str) -> str:
	return f"{CLAIM_CACHE_PREFIX}{nonce}"


def _ensure_install_identity(settings) -> tuple[str, str]:
	"""Return (install_id, install_secret), creating if needed. Does not save."""
	install_id = settings.install_id or secrets.token_hex(16)
	settings.install_id = install_id
	try:
		install_secret = settings.get_password("install_secret") if settings.install_id else None
	except Exception:
		install_secret = None
	if not install_secret:
		install_secret = secrets.token_hex(32)
		settings.install_secret = install_secret
	return install_id, install_secret


# POST-only: this writes and commits (see body), so a GET-able version was
# CSRF-able. `raremachines_settings.js` already calls it via frappe.call, which POSTs.
@frappe.whitelist(methods=["POST"])
def get_pair_defaults() -> dict[str, Any]:
	"""Return non-secret defaults for the RareMachine Settings form."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	base = get_conduit_base_url()
	# Keep Settings row in sync so desk shows the resolved value.
	settings = frappe.get_single("RareMachine Settings")
	if settings.conduit_base_url != base:
		settings.conduit_base_url = base
		settings.save(ignore_permissions=True)
		# Manual commit: durability across a browser redirect — the user leaves this site immediately after, so an uncommitted pairing state would be rolled back and lost.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return {
		"conduit_base_url": base,
		"site_url": frappe.utils.get_url(),
		"connection_status": settings.connection_status,
	}


# POST-only: mints the install identity and flips connection_status.
@frappe.whitelist(methods=["POST"])
def begin_connect_with_conduit() -> dict[str, Any]:
	"""One-click start: open RareMachine (login + confirm workspace), then return here.

	Stores a short-lived claim keyed by nonce (bound to this System Manager).
	No secrets are placed in the browser URL.
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can connect RareMachine."), frappe.PermissionError)

	try:
		base = get_conduit_base_url()
	except frappe.ValidationError:
		_set_error("CONFIG_INVALID")
		raise

	site_url = frappe.utils.get_url()
	settings = frappe.get_single("RareMachine Settings")
	install_id, _install_secret = _ensure_install_identity(settings)

	nonce = secrets.token_urlsafe(24)
	exp = int(time.time()) + CLAIM_TTL_SECONDS
	frappe.cache.set_value(
		_claim_cache_key(nonce),
		{
			"user": frappe.session.user,
			"site_url": site_url,
			"exp": exp,
			"install_id": install_id,
		},
		expires_in_sec=CLAIM_TTL_SECONDS,
	)

	settings.conduit_base_url = base
	settings.connection_status = "Pairing"
	settings.last_error_code = None
	settings.last_error_at = None
	settings.save(ignore_permissions=True)
	# Manual commit: durability across a browser redirect — the user leaves this site immediately after, so an uncommitted pairing state would be rolled back and lost.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	qs = urlencode(
		{
			"site": site_url,
			"nonce": nonce,
			"exp": str(exp),
			"installId": install_id,
		}
	)
	connect_url = f"{base}/integrations/frappe/pair-from-site?{qs}"
	LOGGER.info("pair_begin_connect install_id=%s", install_id)
	return {
		"ok": True,
		"connect_url": connect_url,
		"expires_in": CLAIM_TTL_SECONDS,
	}


@frappe.whitelist(methods=["GET", "POST"])
def finish_browser_pair(
	pair_code: str | None = None,
	nonce: str | None = None,
	replace: int | bool = 0,
) -> None:
	"""Browser return from RareMachine after workspace confirm. Completes pair and redirects to Settings."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can connect RareMachine."), frappe.PermissionError)

	pair_code = (pair_code or frappe.form_dict.get("pair_code") or "").strip()
	nonce = (nonce or frappe.form_dict.get("nonce") or "").strip()
	replace_raw = replace if replace is not None else frappe.form_dict.get("replace") or 0

	settings_path = "/app/raremachine-settings"

	def _redirect(query: str) -> None:
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = f"{settings_path}?{query}"

	if not pair_code or not nonce:
		_redirect("pair_error=CONFIG_INVALID")
		return

	claim = frappe.cache.get_value(_claim_cache_key(nonce))
	if not claim or not isinstance(claim, dict):
		LOGGER.info("pair_finish_failed code=PAIRING_EXPIRED reason=claim_missing")
		_redirect("pair_error=PAIRING_EXPIRED")
		return

	# Prefer same user who started; still allow other System Managers on this site.
	if claim.get("user") and claim.get("user") != frappe.session.user:
		LOGGER.info("pair_finish_user_diff started_by=%s finisher=%s", claim.get("user"), frappe.session.user)

	if int(claim.get("exp") or 0) < int(time.time()):
		frappe.cache.delete_value(_claim_cache_key(nonce))
		_redirect("pair_error=PAIRING_EXPIRED")
		return

	site_now = frappe.utils.get_url()
	if claim.get("site_url") and claim.get("site_url").rstrip("/") != site_now.rstrip("/"):
		LOGGER.info("pair_finish_failed code=CONFIG_INVALID reason=site_mismatch_claim")
		_redirect("pair_error=CONFIG_INVALID")
		return

	# One-time claim
	frappe.cache.delete_value(_claim_cache_key(nonce))

	result = _complete_pair_with_ticket(pair_ticket=pair_code, replace=replace_raw)
	if result.get("ok"):
		LOGGER.info("pair_finish_ok install_id=%s", result.get("install_id"))
		_redirect("paired=1")
	else:
		code = result.get("code") or "CONNECT_FAILED"
		LOGGER.info("pair_finish_failed code=%s", code)
		_redirect(f"pair_error={code}")


def _safe_return_to(return_to: str | None) -> str | None:
	"""Only allow redirect back to configured RareMachine cloud origin."""
	if not return_to or not isinstance(return_to, str):
		return None
	return_to = return_to.strip()
	if not return_to:
		return None
	try:
		base = get_conduit_base_url().rstrip("/")
		u = urlparse(return_to)
		b = urlparse(base)
		if u.scheme not in ("http", "https"):
			return None
		if (u.scheme, u.netloc.lower()) != (b.scheme, b.netloc.lower()):
			return None
		return return_to
	except Exception:
		return None


# allow_guest is only so an unauthenticated visitor can be bounced to /login;
# System Manager is enforced below before anything happens, and the request
# additionally requires a RareMachine-minted one-time pair ticket.
@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])  # nosemgrep: guest-whitelisted-method
def finish_from_web(
	pair_code: str | None = None,
	replace: int | bool = 0,
	return_to: str | None = None,
) -> None:
	"""
	Website-first: RareMachine opens this URL with a short-lived pair ticket.
	System Manager must be logged in (Guest → /login redirect).
	No Frappe-side claim/nonce — ticket is minted on RareMachine for that workspace.
	"""
	pair_code = (pair_code or frappe.form_dict.get("pair_code") or "").strip()
	replace_raw = replace if replace is not None else frappe.form_dict.get("replace") or 0
	return_to = return_to or frappe.form_dict.get("return_to")
	safe_return = _safe_return_to(return_to if isinstance(return_to, str) else None)

	# Preserve full query on login bounce.
	req_path = (
		frappe.request.path if frappe.request else "/api/method/raremachines.api.connect.finish_from_web"
	)
	qs = frappe.request.query_string.decode("utf-8") if frappe.request and frappe.request.query_string else ""
	here = f"{req_path}?{qs}" if qs else req_path

	if frappe.session.user == "Guest":
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = f"/login?redirect-to={quote(here, safe='')}"
		return

	settings_path = "/app/raremachine-settings"

	def _redirect_settings(query: str) -> None:
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = f"{settings_path}?{query}"

	def _redirect_done(ok: bool, code: str | None = None) -> None:
		if safe_return:
			sep = "&" if "?" in safe_return else "?"
			if ok:
				frappe.local.response["type"] = "redirect"
				frappe.local.response["location"] = f"{safe_return}{sep}frappe_site=linked"
			else:
				frappe.local.response["type"] = "redirect"
				frappe.local.response["location"] = f"{safe_return}{sep}error={code or 'CONNECT_FAILED'}"
			return
		if ok:
			_redirect_settings("paired=1")
		else:
			_redirect_settings(f"pair_error={code or 'CONNECT_FAILED'}")

	if "System Manager" not in frappe.get_roles():
		LOGGER.info("pair_finish_from_web_denied not_system_manager user=%s", frappe.session.user)
		_redirect_done(False, "UNAUTHORIZED")
		return

	if not pair_code:
		_redirect_done(False, "CONFIG_INVALID")
		return

	# SECURITY: a GET must never complete the pairing.
	#
	# Frappe does not CSRF-check GET (auth.py's SAFE_HTTP_METHODS), and
	# unlike `finish_browser_pair` — which requires an unguessable, one-time,
	# site-bound nonce cached server-side — this entry point has no second
	# factor at all: the pair ticket is minted on RareMachine, for whichever
	# workspace asked for it.
	#
	# A state-changing GET here would therefore be reachable by cross-site
	# request from any page a System Manager happens to load, with no click
	# and no confirmation. It would bind this site's OAuth client AND its
	# install secret — the sole authenticator for `list_crm_users`, which
	# returns every enabled user's email and full name — to a workspace the
	# admin never chose, with `replace=1` overwriting a legitimate pairing.
	#
	# So GET renders an inert confirmation and only POST changes state, the
	# same shape `oauth_relogin` uses. Frappe DOES enforce CSRF on POST here,
	# since a System Manager is by definition not a Guest session — and a
	# human sees which site is being linked before it happens, which is the
	# real point.
	if frappe.request and frappe.request.method == "GET":
		safe_code = frappe.utils.escape_html(pair_code)
		safe_replace = "1" if str(replace_raw) in ("1", "True", "true") else "0"
		safe_ret = frappe.utils.escape_html(safe_return or "")
		safe_csrf = frappe.utils.escape_html(
			(frappe.session.data.get("csrf_token") or "") if frappe.session else ""
		)
		safe_site = frappe.utils.escape_html(frappe.utils.get_url())
		safe_user = frappe.utils.escape_html(frappe.session.user)
		action = "/api/method/raremachines.api.connect.finish_from_web"
		frappe.respond_as_web_page(
			_("Connect this site to RareMachine?"),
			f"""
			<p>{_("You are about to link this site to RareMachine:")}</p>
			<p><b>{safe_site}</b><br>{_("Signed in as")} {safe_user}</p>
			<p>{_("RareMachine will be able to read and write your CRM records on your behalf. Only continue if you started this from RareMachine.")}</p>
			<form method="POST" action="{action}" style="margin-top:1rem">
				<input type="hidden" name="pair_code" value="{safe_code}">
				<input type="hidden" name="replace" value="{safe_replace}">
				<input type="hidden" name="return_to" value="{safe_ret}">
				<input type="hidden" name="csrf_token" value="{safe_csrf}">
				<button type="submit" class="btn btn-primary">{_("Connect")}</button>
				<a href="/app/raremachine-settings" class="btn btn-default">{_("Cancel")}</a>
			</form>
			""",
			indicator_color="blue",
		)
		return

	result = _complete_pair_with_ticket(pair_ticket=pair_code, replace=replace_raw)
	if result.get("ok"):
		LOGGER.info("pair_finish_from_web_ok install_id=%s", result.get("install_id"))
		_redirect_done(True)
	else:
		code = result.get("code") or "CONNECT_FAILED"
		LOGGER.info("pair_finish_from_web_failed code=%s", code)
		_redirect_done(False, code)


def _complete_pair_with_ticket(
	pair_ticket: str,
	replace: int | bool = 0,
) -> dict[str, Any]:
	"""Internal: mint OAuth Client and POST pair/complete. Not a desk whitelist method."""
	pair_ticket = (pair_ticket or "").strip()
	if not pair_ticket:
		_set_error("CONFIG_INVALID")
		return {"ok": False, "code": "CONFIG_INVALID"}

	try:
		base = get_conduit_base_url()
	except frappe.ValidationError:
		_set_error("CONFIG_INVALID")
		return {"ok": False, "code": "CONFIG_INVALID"}

	redirect_uri = f"{base}/api/integrations/frappe/callback"
	try:
		client_id, client_secret = _ensure_oauth_client(redirect_uri)
	except Exception:
		LOGGER.info("pair_failed code=CONNECT_FAILED oauth_client")
		_set_error("CONNECT_FAILED")
		return {"ok": False, "code": "CONNECT_FAILED"}

	settings = frappe.get_single("RareMachine Settings")
	install_id, install_secret = _ensure_install_identity(settings)

	# Persist the install identity BEFORE calling RareMachine, not after.
	#
	# `_ensure_install_identity` only sets the fields in memory, and the save
	# further down happens once pairing has already succeeded. That ordering
	# was fine while pairing was a single outbound POST — but RareMachine now
	# calls back into `api/org_users.verify_install` DURING that POST, to prove
	# this site is genuinely reachable at the URL being claimed. That callback
	# is a separate request with its own DB connection: it reads
	# `install_secret` from the database, where the value being signed with did
	# not yet exist. Result: the site failed its own ownership challenge and
	# pairing died with SITE_VERIFICATION_FAILED.
	#
	# Committing here is safe regardless of how pairing ends — the install
	# identity is this site's own, not a product of the pairing, and
	# `_ensure_install_identity` reuses it on the next attempt rather than
	# minting a second one.
	settings.save(ignore_permissions=True)
	# Manual commit: the callback above runs in a DIFFERENT request/transaction
	# and cannot see uncommitted work.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	payload = {
		"pairCode": pair_ticket,
		"siteUrl": frappe.utils.get_url(),
		"clientId": client_id,
		"clientSecret": client_secret,
		"installId": install_id,
		"installSecret": install_secret,
		"replace": bool(int(replace)) if replace is not None else False,
	}
	url = f"{base}/api/integrations/frappe/pair/complete"

	LOGGER.info("pair_started install_id=%s", install_id)

	# `requests` via Frappe's own session helper, NOT urllib. Two reasons, and
	# the second one cost a live debugging session:
	#
	# 1. It is the framework convention — Frappe itself uses `requests` in 37
	#    files and `urllib.request` in none. get_request_session() also brings
	#    Retry(total=5) and connection pooling for free.
	# 2. urllib sends `User-Agent: Python-urllib/3.x` by default, and
	#    Cloudflare's Browser Integrity Check blocklists exactly that string —
	#    it 403s the request at the edge, so it never reaches RareMachine at all.
	#    Verified live against a Cloudflare-fronted RareMachine: Python-urllib got
	#    403 (error 1010) on every path, while python-requests, curl and even
	#    NO User-Agent header all got through. BIC is a blocklist, not a
	#    requirement to identify yourself — so this is about not impersonating
	#    a scraper, not about adding a header. Any customer fronting their
	#    RareMachine with Cloudflare would have hit this.
	try:
		session = get_request_session()
		resp = session.post(url, json=payload, timeout=30, headers={"Accept": "application/json"})
	except Exception:
		LOGGER.info("pair_failed code=NETWORK_UNREACHABLE")
		_set_error("NETWORK_UNREACHABLE")
		return {"ok": False, "code": "NETWORK_UNREACHABLE"}
	finally:
		# SECURITY: drop the only reference to the secrets before ANY code that
		# can raise runs below.
		#
		# Frappe logs unhandled exceptions with frame LOCALS attached
		# (frappe/app.py -> log_error_snapshot -> get_traceback(with_context=True)),
		# and its sanitizer redacts dict entries only when the key is EXACTLY
		# one of password/passwd/secret/token/key/pwd. `clientSecret` and
		# `installSecret` match none of those, so this dict would be written to
		# the customer's Error Log in plaintext — permanently, and included in
		# backups and support bundles. That directly contradicts what
		# README.md promises ("never returned by any endpoint or written to a
		# log"), and the Error Log is exactly where a customer would look when
		# pairing misbehaves.
		#
		# It is reachable: `resp.json()` returns a list or a string for a
		# top-level JSON array/string body (a CDN or WAF error page will do
		# this), and `.get(...)` on that raises AttributeError, which the
		# `except ValueError` below does NOT catch.
		del payload

	if resp.status_code >= 400:
		try:
			body = resp.json()
			# `resp.json()` is not necessarily a dict — a top-level JSON array
			# or string (what a CDN/WAF error page often returns) parses fine
			# and then `.get` raises AttributeError, which `except ValueError`
			# does not catch. Treat anything that isn't an object as "no code".
			remote = body.get("code") if isinstance(body, dict) else None
		except ValueError:
			remote = None

		if remote in ALLOWED_ERROR_CODES:
			code = remote
		elif resp.status_code == 409:
			code = "SITE_MISMATCH"
		elif resp.status_code in (401, 403):
			# A 401/403 whose body RareMachine did not produce means something in
			# FRONT of RareMachine refused us (CDN/WAF), not an auth decision by
			# RareMachine. Reporting UNAUTHORIZED here is what sent us hunting a
			# non-existent Frappe role problem while Cloudflare was silently
			# 403-ing at the edge. Unreachable is the honest description.
			code = "NETWORK_UNREACHABLE"
		else:
			code = "CONNECT_FAILED"
		LOGGER.info("pair_failed code=%s http_status=%s", code, resp.status_code)
		_set_error(code)
		return {"ok": False, "code": code}

	try:
		data = resp.json() if resp.text else {}
	except ValueError:
		data = None
	# Same non-dict guard as the >=400 branch above: a successful status with a
	# JSON array/string body must be a clean CONNECT_FAILED, not an
	# AttributeError that ends up in the Error Log.
	if not isinstance(data, dict):
		LOGGER.info("pair_failed code=CONNECT_FAILED")
		_set_error("CONNECT_FAILED")
		return {"ok": False, "code": "CONNECT_FAILED"}

	if not data.get("ok"):
		code = data.get("code") if data.get("code") in ALLOWED_ERROR_CODES else "CONNECT_FAILED"
		LOGGER.info("pair_failed code=%s", code)
		_set_error(code)
		return {"ok": False, "code": code}

	settings.enabled = 1
	settings.conduit_base_url = base
	settings.install_id = data.get("install_id") or install_id
	settings.connection_status = "Active"
	settings.paired_at = frappe.utils.now_datetime()
	settings.last_error_code = None
	settings.last_error_at = None
	settings.install_secret = install_secret
	settings.save(ignore_permissions=True)
	# Manual commit: durability across a browser redirect — the user leaves this site immediately after, so an uncommitted pairing state would be rolled back and lost.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	LOGGER.info("pair_completed install_id=%s", settings.install_id)
	return {"ok": True, "status": "active", "install_id": settings.install_id}


# POST-only: this unpairs the site and wipes install_secret. Frappe does not
# CSRF-check GET, so a GET-able version would let any page an admin happens
# to visit silently disconnect RareMachine.
@frappe.whitelist(methods=["POST"])
def disconnect() -> dict[str, Any]:
	"""Clear pairing on this site."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can disconnect RareMachine."), frappe.PermissionError)

	settings = frappe.get_single("RareMachine Settings")
	settings.enabled = 0
	settings.connection_status = "Disconnected"
	settings.install_id = None
	settings.install_secret = None
	settings.paired_at = None
	settings.last_error_code = None
	settings.last_error_at = None
	# Keep conduit_base_url as resolved cloud for next pair.
	settings.conduit_base_url = get_conduit_base_url()
	settings.save(ignore_permissions=True)
	# Manual commit: durability across a browser redirect — the user leaves this site immediately after, so an uncommitted pairing state would be rolled back and lost.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	LOGGER.info("pair_disconnected")
	return {"ok": True, "status": "disconnected"}


def _is_safe_oauth_authorize_url(url: str) -> bool:
	"""Only allow redirect to this site's Frappe OAuth authorize endpoint."""
	try:
		parsed = urlparse((url or "").strip())
	except Exception:
		return False
	if parsed.scheme not in ("http", "https"):
		return False
	site = urlparse(frappe.utils.get_url())
	# Host must match this site (ignore port differences only if both lack userinfo).
	if (parsed.hostname or "").lower() != (site.hostname or "").lower():
		return False
	path = (parsed.path or "").rstrip("/")
	return path.endswith("/api/method/frappe.integrations.oauth2.authorize")


# allow_guest because this is reached by a cross-site redirect from RareMachine
# before the user has necessarily signed in. It performs NO state change on
# GET — it only renders a confirmation; the logout lives in the POST-only,
# CSRF-checked oauth_relogin_switch below.
@frappe.whitelist(allow_guest=True, methods=["GET"])  # nosemgrep: guest-whitelisted-method
def oauth_relogin(authorize_url: str | None = None) -> None:
	"""
	Ask the human which Frappe account to authorize as. Changes NO state.

	Personal RareMachine connect must run as the human's own Frappe user. Desk is
	often still Administrator after site pairing — authorizing from that session
	mints a token as `admin@example.com`, and RareMachine then rejects it on the
	email match.

	*** Why this is GET-with-no-side-effects, and not a logout ***

	This endpoint is reached by a cross-site browser redirect from RareMachine, so
	it cannot carry Frappe's CSRF token and cannot be POST-only. It must
	therefore perform NO state change. A GET that logged the user out would
	be reachable by cross-site request from any third-party page, silently
	ending the session of any user of the customer's site. Frappe's own
	`logout` is `@frappe.whitelist(allow_guest=True, methods=["POST"])`
	(frappe/handler.py) for exactly that reason.

	So the GET renders a confirmation instead. The actual logout lives in
	`oauth_relogin_switch` below, which is POST-only and receives Frappe's CSRF
	token from the form rendered here — a cross-site attacker cannot forge it.

	Bonus: showing the signed-in identity here is what the OAuth consent screen
	was previously being monkeypatched to do.
	"""
	url = (authorize_url or frappe.form_dict.get("authorize_url") or "").strip()
	if not _is_safe_oauth_authorize_url(url):
		frappe.throw(_("Invalid OAuth authorize URL."), frappe.ValidationError)

	current = frappe.session.user if frappe.session else "Guest"

	# Not signed in: nothing to disambiguate and nothing to log out. Frappe's
	# own authorize endpoint bounces Guest to /login, so just hand off.
	if not current or current == "Guest":
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = url
		return

	full_name = frappe.db.get_value("User", current, "full_name") or current
	csrf = (frappe.session.data.get("csrf_token") or "") if frappe.session else ""
	switch_action = "/api/method/raremachines.api.connect.oauth_relogin_switch"

	# `frappe.respond_as_web_page` is the framework's own mechanism — no core
	# template is shadowed and no framework function is patched. Every
	# interpolated value is escaped: identities via escape_html, and the URLs
	# are attribute-escaped after already passing _is_safe_oauth_authorize_url.
	safe_user = frappe.utils.escape_html(current)
	safe_name = frappe.utils.escape_html(full_name)
	safe_url = frappe.utils.escape_html(url)
	safe_csrf = frappe.utils.escape_html(csrf)
	safe_action = frappe.utils.escape_html(switch_action)

	html = f"""
		<div style="max-width:32rem">
			<p>{_("You are signed in to this site as")}
				<strong>{safe_name}</strong> (<code>{safe_user}</code>).</p>
			<p>{_("RareMachine will connect the account you authorize with. It must be your own Frappe login, matching your RareMachine email.")}</p>
			<p style="margin-top:1.5rem">
				<a href="{safe_url}" class="btn btn-primary">
					{_("Continue as")} {safe_name}</a>
			</p>
			<form method="POST" action="{safe_action}" style="margin-top:0.75rem">
				<input type="hidden" name="authorize_url" value="{safe_url}">
				<input type="hidden" name="csrf_token" value="{safe_csrf}">
				<button type="submit" class="btn btn-default">
					{_("Sign in as a different user")}</button>
			</form>
		</div>
	"""
	frappe.respond_as_web_page(_("Connect your Frappe account"), html, indicator_color="blue")


@frappe.whitelist(methods=["POST"])
def oauth_relogin_switch(authorize_url: str | None = None) -> None:
	"""
	Log out, then send the user to /login so they can authorize as themselves.

	POST-only and not `allow_guest`, so Frappe validates a CSRF token before
	this runs (frappe/auth.py `validate_csrf_token`). That is what makes the
	logout un-forgeable from a third-party page. Reached only from the form
	rendered by `oauth_relogin` above.
	"""
	url = (authorize_url or frappe.form_dict.get("authorize_url") or "").strip()
	if not _is_safe_oauth_authorize_url(url):
		frappe.throw(_("Invalid OAuth authorize URL."), frappe.ValidationError)

	previous = frappe.session.user if frappe.session else "Guest"
	if previous and previous != "Guest":
		try:
			frappe.local.login_manager.logout()
			LOGGER.info("oauth_relogin_logout previous_user=%s", previous)
		except Exception:
			LOGGER.info("oauth_relogin_logout_failed previous_user=%s", previous)
			frappe.log_error(title="raremachines: oauth relogin logout failed")

	# Belt-and-suspenders: clear session cookies even if logout was partial.
	try:
		from frappe.auth import clear_cookies

		clear_cookies()
	except Exception:
		frappe.log_error(title="raremachines: oauth relogin clear_cookies failed")

	# Always go through login so the human picks the account RareMachine expects.
	login_qs = urlencode({"redirect-to": url})
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = f"/login?{login_qs}"


# --- RareMachine → Frappe WhatsApp relay -----------------------------------
#
# Two receivers for the two gaps closed by the "WhatsApp relay" plan
# (2026-08-24): RareMachine now owns a client's inbound WhatsApp webhook
# directly (a customer's own Meta app points at RareMachine's URL, not this
# site's), so this site needs an authenticated way to (a) learn about an
# inbound sender RareMachine decided is an external lead, not one of this
# workspace's own reps, and (b) keep receiving delivery/read-receipt status
# updates for messages THIS site sent (bulk sends, template sends), which
# Meta now delivers only to RareMachine's webhook instead of this site's own.
#
# Both reuse `org_users.py`'s `_verify_install_signature()` verbatim — same
# HMAC-SHA256-over-`timestamp.body`, same `install_secret`, same
# `SIGNATURE_MAX_SKEW_SECONDS` replay bound RareMachine's other
# server-to-server calls into this site already use. `allow_guest=True` for
# the same reason as `list_crm_users`/`verify_install`: RareMachine calls
# these server-to-server, with no Frappe session.
from raremachines.api.org_users import _verify_install_signature


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def receive_whatsapp_lead() -> None:
	"""
	An inbound WhatsApp sender RareMachine classified as an external-lead
	candidate (not a verified rep of this workspace) — RareMachine makes
	that classification and calls this endpoint for the result.

	Inserts a real `WhatsApp Message` (type Incoming) rather than creating a
	CRM Lead directly here — deliberately, so this reuses every bit of
	already-shipped WhatsApp machinery on this site instead of
	reimplementing a slice of it: `crm.api.whatsapp.validate`'s contact
	lookup runs, `raremachines.api.whatsapp_lead.ensure_lead_for_unmatched_sender`
	(already built, already tested) creates the Lead+Contact on no match,
	the message shows up in that Lead's WhatsApp thread in the CRM UI, and
	any `WhatsApp Notification` DocType-Event automation an admin has
	configured on this site fires exactly as it would for a natively
	received message.

	Idempotent by `message_id`: the caller may retry a delivery (its own
	job queue may re-attempt), and a second delivery of the same Meta
	message must not create a second `WhatsApp Message`/Lead.

	**Extended (guided-intake plan, 2026-08-26):** when the payload carries
	the fuller guided-intake fields (name/company/email/marketType/etc — all
	optional, since the legacy single-message forward still calls this with
	only waId/messageId/text), the newly-linked Lead is updated with them in
	the SAME request, after the `WhatsApp Message` insert has resolved
	`reference_name` via the existing validate-hook chain. `brochure_pdf` is
	resolved from `RareMachine Settings`'s admin-configured file, never
	passed in by the caller. `whatsappBrochureSent` records whether
	RareMachine itself already sent the brochure over WhatsApp within the
	live session (the external-intake flow's own send) — this endpoint only
	ever RECORDS that outcome, never sends anything itself.

	**Extended (transcript tracking, 2026-08-27):** `type` ('Incoming' or
	'Outgoing', default 'Incoming') and `contentType`/`attach` let RareMachine
	log EVERY message of a guided-intake conversation, not just the final
	summary — so this site's native WhatsApp chat panel mirrors the real
	WhatsApp thread message-for-message, literal text, both directions.

	Outgoing messages (the bot's own sends — gate prompt, Flow trigger,
	brochure document, closing text) are inserted via `db_insert()` rather
	than `insert()`, deliberately: `WhatsAppMessage.before_insert()`
	(`frappe_whatsapp`'s own controller, not ours to change) unconditionally
	calls `send_outgoing()` for ANY `type="Outgoing"` doc, which fires a
	REAL second send to Meta — for a message Conduit already sent directly,
	that would double-deliver it to the customer. `db_insert()` writes the
	row via raw SQL only, skipping that hook (and every other controller
	hook, including `validate`) entirely. Reference-doctype resolution —
	normally done by `crm.api.whatsapp.validate()`'s own `validate` hook,
	also skipped by `db_insert()` — is replicated manually below using the
	exact same lookup that hook itself calls. `ensure_lead_for_unmatched_sender`
	(raremachines' OWN `validate` hook, chained after crm's) is deliberately
	NOT replicated here — by the time any Outgoing message exists for a
	conversation, the matching Incoming message that started it has always
	already run through the real `.insert()` path first and created the
	Lead, so there is never an unmatched sender left for an Outgoing entry
	to resolve.
	"""
	_verify_install_signature()

	if "frappe_whatsapp" not in frappe.get_installed_apps():
		frappe.throw(_("This site does not have frappe_whatsapp installed."), frappe.ValidationError)

	wa_id = (frappe.form_dict.get("waId") or "").strip()
	message_id = (frappe.form_dict.get("messageId") or "").strip()
	text = frappe.form_dict.get("text") or ""
	msg_type = (frappe.form_dict.get("type") or "Incoming").strip()
	if msg_type not in ("Incoming", "Outgoing"):
		msg_type = "Incoming"
	content_type = (frappe.form_dict.get("contentType") or "text").strip()
	attach = (frappe.form_dict.get("attach") or "").strip()

	if not wa_id or not message_id:
		frappe.throw(_("waId and messageId are required."), frappe.ValidationError)

	if frappe.db.exists("WhatsApp Message", {"message_id": message_id}):
		return

	from frappe_whatsapp.utils import get_whatsapp_account

	whatsapp_account = get_whatsapp_account(account_type="outgoing" if msg_type == "Outgoing" else "incoming")

	# The `exists()` check above is a fast path, not the actual guarantee —
	# under a genuinely concurrent retry of the same Meta webhook delivery,
	# two requests can both pass it before either inserts. The real
	# idempotency guarantee is the DB-level unique constraint on
	# `message_id` (`install.py`'s `_ensure_whatsapp_message_id_unique`);
	# a `UniqueValidationError` here means "someone else already inserted
	# this exact message_id a moment ago" — a benign no-op, not an error.
	try:
		if msg_type == "Outgoing":
			msg = frappe.new_doc("WhatsApp Message")
			msg.update(
				{
					"type": "Outgoing",
					"to": wa_id,
					"message": text,
					"message_id": message_id,
					"content_type": content_type,
					"status": "Success",
				}
			)
			if attach:
				msg.attach = attach
			if whatsapp_account:
				msg.whatsapp_account = whatsapp_account.name
			msg.db_insert()

			from crm.integrations.api import get_contact_lead_or_deal_from_number

			reference_name, reference_doctype = get_contact_lead_or_deal_from_number(wa_id)
			if reference_doctype and reference_name:
				frappe.db.set_value(
					"WhatsApp Message",
					msg.name,
					{"reference_doctype": reference_doctype, "reference_name": reference_name},
				)
		else:
			doc_fields = {
				"doctype": "WhatsApp Message",
				"type": "Incoming",
				"from": wa_id,
				"message": text,
				"message_id": message_id,
				"content_type": content_type,
			}
			if attach:
				doc_fields["attach"] = attach
			if whatsapp_account:
				doc_fields["whatsapp_account"] = whatsapp_account.name

			msg = frappe.get_doc(doc_fields)
			msg.insert(ignore_permissions=True)
			reference_doctype = msg.reference_doctype
			reference_name = msg.reference_name
	except frappe.UniqueValidationError:
		return

	if reference_doctype == "CRM Lead" and reference_name:
		_apply_guided_intake_fields(reference_name)


# Frappe's standard `Data` fieldtype max length — `first_name`/`organization`/
# `email` on `CRM Lead` are all plain `Data` fields. Found in review
# (2026-08-31): this endpoint is HMAC-authenticated (not open to the public
# internet), but nothing capped these caller-supplied values before they hit
# `lead.save()` — a value longer than this would fail the save outright
# rather than silently corrupt anything, but truncating up front is cheaper
# than a failed save for a value that's going to be cut off either way.
_INTAKE_FIELD_MAX_LENGTH = 140


def _apply_guided_intake_fields(lead_name: str) -> None:
	"""Best-effort — a lead the WhatsApp Message hook chain just resolved
	always exists, but every intake field is optional (the legacy
	single-message forward path never sends any of these)."""
	name = (frappe.form_dict.get("name") or "").strip()[:_INTAKE_FIELD_MAX_LENGTH]
	company = (frappe.form_dict.get("company") or "").strip()[:_INTAKE_FIELD_MAX_LENGTH]
	email = (frappe.form_dict.get("email") or "").strip()[:_INTAKE_FIELD_MAX_LENGTH]
	market_type = (frappe.form_dict.get("marketType") or "").strip()
	regulated_status = (frappe.form_dict.get("regulatedStatus") or "").strip()
	certificate = (frappe.form_dict.get("certificate") or "").strip()
	enquiry_type = (frappe.form_dict.get("enquiryType") or "").strip()
	whatsapp_brochure_sent = bool(frappe.form_dict.get("whatsappBrochureSent"))

	if not any([name, company, email, market_type, regulated_status, certificate, enquiry_type]):
		return

	lead = frappe.get_doc("CRM Lead", lead_name)
	if name:
		lead.first_name = name
	if company:
		lead.organization = company
	if email:
		lead.email = email
	if enquiry_type in ("Product Enquiry", "Support"):
		lead.enquiry_type = enquiry_type
	if market_type in ("Domestic", "Export"):
		lead.market_type = market_type
		lead.brochure_type = market_type
		lead.brochure_pdf = _resolve_brochure_pdf(market_type)
	if regulated_status in ("Regulated", "Non-Regulated", "Not Sure"):
		lead.regulated_status = regulated_status
	if certificate and frappe.db.exists("Export Certificate", certificate):
		lead.certificate = certificate
	if whatsapp_brochure_sent:
		lead.whatsapp_brochure_sent = 1

	lead.save(ignore_permissions=True)


def _resolve_brochure_pdf(market_type: str) -> str | None:
	"""The admin-configured 'global default brochure' — Desk-editable on
	`RareMachine Settings`, never hardcoded here."""
	fieldname = "domestic_brochure_file" if market_type == "Domestic" else "export_brochure_file"
	return frappe.db.get_single_value("RareMachine Settings", fieldname)


def _absolute_public_file_url(file_url: str | None, log_title: str) -> str | None:
	if not file_url:
		return None
	if file_url.startswith("/private/"):
		frappe.log_error(title=log_title, message=f"file_url={file_url}")
		return None
	if file_url.startswith("http"):
		return file_url
	return frappe.utils.get_url() + quote(file_url, safe="/")


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def receive_brochure_choice() -> None:
	"""
	Internal (rep) flow: a rep answered RareMachine's "which brochure for
	this lead?" prompt after creating a new Lead (typed command or
	business-card scan). Sets `brochure_type`/`brochure_pdf` on the
	EXISTING Lead — the actual (templated) send is Frappe's own
	`WhatsApp Notification`/`Notification` rows reacting to that field
	change, not this endpoint.
	"""
	_verify_install_signature()

	lead_name = (frappe.form_dict.get("leadId") or "").strip()
	market_type = (frappe.form_dict.get("brochureType") or "").strip()
	if not lead_name or market_type not in ("Domestic", "Export"):
		frappe.throw(
			_("leadId and a valid brochureType (Domestic/Export) are required."), frappe.ValidationError
		)
	if not frappe.db.exists("CRM Lead", lead_name):
		frappe.throw(_("Unknown Lead."), frappe.ValidationError)

	lead = frappe.get_doc("CRM Lead", lead_name)
	lead.brochure_type = market_type
	lead.brochure_pdf = _resolve_brochure_pdf(market_type)
	lead.save(ignore_permissions=True)


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def list_export_certificates() -> dict:
	"""Admin-editable certificate list for the export-market guided-intake
	step — read synchronously by RareMachine (short-TTL cached there), not
	the async job pattern the lead/status relay uses, since this renders a
	chat message while a real person is waiting.
	"""
	_verify_install_signature()

	if not frappe.db.exists("DocType", "Export Certificate"):
		return {"certificates": []}

	rows = frappe.get_all("Export Certificate", filters={"enabled": 1}, fields=["name"], order_by="name asc")
	return {"certificates": [r.name for r in rows]}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def get_brochure_pdf_url() -> dict:
	"""RareMachine needs a URL Meta's Graph API can fetch to actually send the
	brochure as a WhatsApp document message — a Frappe-internal file path
	isn't enough. Returns the ADMIN-CONFIGURED brochure's full public URL, or
	`{"url": null}` if none is set or the file isn't public.

	The uploaded file MUST be public (not a private Attach) — Meta's
	document-fetch request carries no Frappe session, so a private file's
	signed-key requirement would make every send fail. This endpoint does
	not silently work around that; a private file is treated the same as
	"no file configured".
	"""
	_verify_install_signature()

	market_type = (frappe.form_dict.get("marketType") or "").strip()
	if market_type not in ("Domestic", "Export"):
		frappe.throw(_("marketType must be Domestic or Export."), frappe.ValidationError)

	file_url = _resolve_brochure_pdf(market_type)
	absolute_url = _absolute_public_file_url(
		file_url,
		"raremachines: brochure file is private, cannot be sent over WhatsApp",
	)
	return {"url": absolute_url}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def get_company_profile_pdf_url() -> dict:
	"""Return the admin-configured company profile PDF's public URL, if set."""
	_verify_install_signature()

	file_url = frappe.db.get_single_value("RareMachine Settings", "company_profile_file")
	absolute_url = _absolute_public_file_url(
		file_url,
		"raremachines: company profile file is private, cannot be sent over WhatsApp",
	)
	return {"url": absolute_url}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def receive_whatsapp_status() -> None:
	"""
	A Meta delivery/read-receipt event for a message THIS site sent (bulk
	send, template send, or any other `frappe_whatsapp` outbound path),
	relayed here because Meta now delivers `statuses[]` callbacks only to
	RareMachine's webhook, not this site's. Caller may retry; treat a
	duplicate delivery of the same `message_id` as a no-op.

	Deliberately NOT a call into `frappe_whatsapp.utils.webhook.
	update_message_status` — that function raises on no matching message,
	which is the EXPECTED, common case here: most status events RareMachine
	forwards are for messages RareMachine itself sent (a reply to a
	verified rep), which this site never has a record of. No match is a
	silent no-op, not an error.
	"""
	_verify_install_signature()

	message_id = (frappe.form_dict.get("messageId") or "").strip()
	status = (frappe.form_dict.get("status") or "").strip()
	conversation_id = frappe.form_dict.get("conversationId")
	if not message_id or not status:
		frappe.throw(_("messageId and status are required."), frappe.ValidationError)

	name = frappe.db.get_value("WhatsApp Message", {"message_id": message_id})
	if not name:
		return

	doc = frappe.get_doc("WhatsApp Message", name)
	doc.status = status
	if conversation_id:
		doc.conversation_id = conversation_id
	doc.save(ignore_permissions=True)
