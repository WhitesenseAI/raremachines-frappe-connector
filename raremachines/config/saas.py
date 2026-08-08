# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""RareMachines SaaS URL resolution — not editable by end-user admins."""

from __future__ import annotations

from urllib.parse import urlparse

import frappe
from frappe import _

# Hosts for which plaintext http:// is tolerated (local development only).
# Compared against the parsed hostname — never as a string prefix.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

# Production SaaS origin. Override with site_config:
#   "raremachines_base_url": "https://app.example.com"
# Local bench without site_config: developer_mode → http://localhost:3000
DEFAULT_CONDUIT_BASE_URL = "https://whitesense.in"
LOCAL_DEV_CONDUIT_BASE_URL = "http://localhost:3000"


def normalize_conduit_base_url(raw: str) -> str:
	url = (raw or "").strip().rstrip("/")
	if not url:
		frappe.throw(_("RareMachines base URL is not configured."), frappe.ValidationError)
	if url.startswith("https://"):
		return url
	# SECURITY: match the HOST, not a string prefix. `startswith("http://127.0.0.1")`
	# is also true of `http://127.0.0.1.evil.com`, and `startswith("http://localhost")`
	# of `http://localhost.attacker.tld` — either one walks straight past the
	# https requirement below and makes the site POST its clientSecret and
	# installSecret, in plaintext, to a remote host it does not control. The
	# same origin is then trusted by `_safe_return_to` for browser redirects.
	if urlparse(url).hostname in LOOPBACK_HOSTS:
		return url
	if url.startswith("http://"):
		frappe.throw(
			_("RareMachines base URL must use https:// (or http://localhost for development)"),
			frappe.ValidationError,
		)
	frappe.throw(
		_("RareMachines base URL must start with https:// (or http://localhost for development)"),
		frappe.ValidationError,
	)
	return url


def get_conduit_base_url() -> str:
	"""Resolve which RareMachines cloud this site pairs with.

	Order:
	1. site_config / common_site_config `raremachines_base_url` (ops override),
	   falling back to the pre-rename `conduit_base_url`
	2. developer_mode → local RareMachines
	3. baked-in SaaS default
	"""
	# `raremachines_base_url` is the current key; `conduit_base_url` is still
	# honoured so a site configured before the rename keeps working rather than
	# silently falling back to the baked-in default.
	override = frappe.conf.get("raremachines_base_url") or frappe.conf.get("conduit_base_url")
	if override:
		return normalize_conduit_base_url(str(override))
	if frappe.conf.get("developer_mode"):
		return LOCAL_DEV_CONDUIT_BASE_URL
	return DEFAULT_CONDUIT_BASE_URL
