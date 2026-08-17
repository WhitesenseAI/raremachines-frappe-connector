# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""
List the CRM users on this site, so a RareMachine workspace admin can see who
already works in this Frappe site and invite them.

This only ever SUGGESTS invitations. It never provisions a RareMachine account and
never grants anyone access — a human admin still clicks Invite on the RareMachine
side, and each invited person still completes their own Frappe OAuth before any
CRM tool runs as them.

*** What leaves this site ***

The response contains the email address and full name of every enabled System
User who can read CRM Lead. That is real personal data crossing to RareMachine, and
it is the most sensitive thing this app discloses — it is documented in the
app's README under Data & privacy for exactly that reason.

Called by RareMachine's SERVER, never by a browser and never from a logged-in Desk
session (the RareMachine admin viewing the picker need not be logged into Frappe at
all). It is therefore `allow_guest=True` and carries its own authentication:
HMAC-SHA256 over `timestamp.body`, keyed by this site's `install_secret` — the
shared secret minted once at pair time by `_ensure_install_identity` in
connect.py.

Secrecy: never log install_secret or request bodies (same discipline as
connect.py).
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from typing import Any

import frappe
from frappe import _

# RareMachine signs `timestamp.body`; anything older than this is refused as a
# replay. Five minutes absorbs ordinary clock drift between two hosts
# without leaving a usefully wide window.
SIGNATURE_MAX_SKEW_SECONDS = 300


def _verify_install_signature() -> None:
	"""Fails closed: no RareMachine Settings, no install_secret, or a missing/wrong
	signature all reject. There is no unsigned fallback — this endpoint returns
	real user PII (email, name), so it never had a permissive mode.

	The signed material is `timestamp.body`, not the body alone. Signing only
	the body makes a captured request replayable forever, which for a PII
	disclosure endpoint means one intercepted call is a standing subscription
	to the customer's staff roster. The timestamp is inside the HMAC, so it
	cannot be edited without the secret.
	"""
	settings = frappe.get_single("RareMachine Settings")
	secret = settings.get_password("install_secret") if settings.install_id else None
	if not secret:
		frappe.throw(_("This site is not paired with RareMachine."), frappe.PermissionError)

	signature = frappe.get_request_header("X-Conduit-Signature") or ""
	timestamp = frappe.get_request_header("X-Conduit-Timestamp") or ""
	raw_body = frappe.request.get_data() or b""

	if not signature or not timestamp:
		frappe.throw(_("Invalid signature."), frappe.PermissionError)

	try:
		ts = float(timestamp)
	except TypeError, ValueError:
		frappe.throw(_("Invalid signature."), frappe.PermissionError)
	# `float("nan")` parses happily, and EVERY comparison against NaN is False —
	# including `skew > MAX`, so a NaN timestamp would sail past the expiry
	# check. Not exploitable (the timestamp is inside the HMAC, so it cannot be
	# substituted without the secret), but the guard should not depend on that.
	if not math.isfinite(ts):
		frappe.throw(_("Invalid signature."), frappe.PermissionError)
	skew = abs(time.time() - ts)
	if skew > SIGNATURE_MAX_SKEW_SECONDS:
		frappe.throw(_("Request expired."), frappe.PermissionError)

	signed = timestamp.encode("utf-8") + b"." + raw_body
	expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
	# `compare_digest` raises TypeError on a non-ASCII str, and werkzeug decodes
	# headers as latin-1 — so a header like `X-Conduit-Signature: é` would 500
	# and write `expected` (the CORRECT HMAC for that timestamp) into the
	# Error Log via Frappe's traceback-with-locals. Reject non-ASCII first.
	if not signature.isascii() or not hmac.compare_digest(expected, signature):
		frappe.throw(_("Invalid signature."), frappe.PermissionError)


# allow_guest because RareMachine calls this server-to-server, with no Frappe
# session. It is NOT unauthenticated: _verify_install_signature() requires a
# valid HMAC over `timestamp.body` keyed by this site's install_secret, and
# fails closed if the site is unpaired.
@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def list_crm_users() -> dict[str, Any]:
	"""
	Enabled System Users who can read CRM Lead — i.e. have SOME CRM-capable
	role, without hardcoding which one. Same has_permission-per-user
	discipline as get_capability_permissions (permissions.py): a custom role
	pack with the right DocPerms is included exactly like a stock one, and
	nothing here is a security boundary — RareMachine's own invite flow still
	requires a human admin to click Invite, and personal OAuth (D12) still
	requires that person to actually log into Frappe as themselves before
	any tool call executes. This is a UX suggestion list, not an allowlist.
	"""
	_verify_install_signature()

	users = frappe.get_all(
		"User",
		filters={
			"enabled": 1,
			"user_type": "System User",
			"name": ["not in", ["Administrator", "Guest"]],
		},
		fields=["name", "email", "full_name"],
	)

	crm_users = [
		{"email": u.email or u.name, "fullName": u.full_name or u.name}
		for u in users
		if frappe.has_permission("CRM Lead", "read", user=u.name)
	]

	return {"users": crm_users}


# allow_guest for the same reason as `list_crm_users`: RareMachine calls this
# server-to-server with no Frappe session, authenticated by the per-site HMAC.
@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def verify_install() -> dict[str, Any]:
	"""
	Prove to RareMachine that this site is genuinely reachable at the URL claimed
	for it, by echoing an HMAC over a nonce RareMachine chose.

	This exists because RareMachine's `pair/complete` previously accepted a
	`siteUrl` on the caller's word alone — it made no network call at all —
	and then wrote that host onto the connection as its account identity.
	Anyone could therefore claim any host, including one belonging to another
	company, and squat the mapping that decides which workspace a site
	resolves to.

	The proof is NOT the `install_secret` itself (the caller supplies that in
	the same pairing request, so on its own it proves nothing). The proof is
	that the HTTP round-trip carrying this nonce arrives HERE — at the host
	that was claimed. Someone claiming a site they do not control cannot
	answer, because the request goes to that site's real server.

	Returns only a derived value; never the secret, and no site data.
	"""
	_verify_install_signature()

	nonce = (frappe.form_dict.get("nonce") or "").strip()
	# Bound the input: this value is HMAC'd, so an unbounded body would be an
	# easy way to make this endpoint do unbounded work.
	if not nonce or len(nonce) > 256:
		frappe.throw(_("Invalid nonce."), frappe.ValidationError)

	settings = frappe.get_single("RareMachine Settings")
	secret = settings.get_password("install_secret", raise_exception=False)
	if not secret:
		frappe.throw(_("Invalid signature."), frappe.PermissionError)

	proof = hmac.new(secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()
	return {"proof": proof, "site_url": frappe.utils.get_url()}
