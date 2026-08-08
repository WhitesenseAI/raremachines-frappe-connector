# Copyright (c) 2026, Whitefield Labs and contributors
"""CRM capability permission probe — works with custom roles (no hardcoded role names)."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

_CRM_DOCTYPES = (
	"CRM Lead",
	"CRM Deal",
	"CRM Organization",
	"CRM Task",
	"FCRM Note",
	"CRM Call Log",
	"Contact",
	"CRM Deal Status",
)


@frappe.whitelist()
def get_capability_permissions() -> dict[str, Any]:
	"""
	Return current user roles + per-DocType create/read/write using Frappe's
	permission engine. Custom roles with DocPerm rows are honored; no hardcoding
	of "Sales User".
	"""
	if frappe.session.user in (None, "Guest"):
		frappe.throw(_("Authentication required"), frappe.PermissionError)

	roles = [r for r in frappe.get_roles() if r not in ("Guest", "All")]
	doctypes: dict[str, dict[str, bool]] = {}
	for dt in _CRM_DOCTYPES:
		if not frappe.db.exists("DocType", dt):
			continue
		# The return value is used as DATA, not as a gate — that is the whole
		# purpose of this endpoint. It reports what the caller may do so RareMachines
		# can decide which tools to SHOW; it is never the thing that permits an
		# action. Every actual CRM call is made later with that user's own OAuth
		# token, and Frappe enforces their DocPerms and User Permissions on the
		# wire at that point. Passing `throw=True` here (what the rule wants)
		# would turn a "what can you do?" probe into a hard failure the first
		# time a user lacks any one permission, which is precisely the opposite
		# of the intent.
		doctypes[dt] = {
			"read": bool(frappe.has_permission(dt, "read")),  # nosemgrep: unchecked-frappe-permission-call
			"write": bool(frappe.has_permission(dt, "write")),  # nosemgrep: unchecked-frappe-permission-call
			"create": bool(
				frappe.has_permission(dt, "create")  # nosemgrep: unchecked-frappe-permission-call
			),
		}

	return {
		"user": frappe.session.user,
		"roles": roles,
		"doctypes": doctypes,
	}
