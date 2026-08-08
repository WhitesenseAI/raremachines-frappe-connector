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

# The hosted RareMachines service. Every install talks to this unless the site
# explicitly overrides it in site_config:
#   "raremachines_base_url": "http://localhost:3000"
DEFAULT_CONDUIT_BASE_URL = "https://whitesense.in"


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
	"""Resolve which RareMachines service this site pairs with.

	Order:
	1. site_config / common_site_config `raremachines_base_url` (explicit
	   override), falling back to the pre-rename `conduit_base_url`
	2. the hosted service

	There is deliberately NO inference from `developer_mode`. That flag belongs
	to the SITE OWNER, not to this app: plenty of self-hosted benches and
	staging sites run with it on, and some production sites never turn it off.
	Reading it here would silently point such a site at a port on its OWN
	server — and `install.py` writes the resolved value into RareMachines
	Settings at install time, so the wrong value would then stick.

	`raremachines_base_url` is already the explicit way to point elsewhere, and
	`normalize_conduit_base_url` permits http:// for loopback hosts, so local
	development is fully served by it. One explicit mechanism beats two, one of
	which guesses.
	"""
	# `raremachines_base_url` is the current key; `conduit_base_url` is still
	# honoured so a site configured before the rename keeps working rather than
	# silently falling back to the baked-in default.
	override = frappe.conf.get("raremachines_base_url") or frappe.conf.get("conduit_base_url")
	if override:
		return normalize_conduit_base_url(str(override))
	return DEFAULT_CONDUIT_BASE_URL
