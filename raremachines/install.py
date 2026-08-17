# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe

from raremachines.config.saas import get_conduit_base_url


def after_install():
	_ensure_settings()
	_ensure_workspace()
	_link_frappe_crm_workspace()


def after_migrate():
	"""Keep nav + defaults healthy after migrate."""
	_ensure_settings()
	_ensure_workspace()
	_link_frappe_crm_workspace()


def _ensure_settings():
	base = get_conduit_base_url()
	if not frappe.db.exists("DocType", "RareMachine Settings"):
		return
	if not frappe.db.exists("RareMachine Settings", "RareMachine Settings"):
		doc = frappe.get_doc(
			{
				"doctype": "RareMachine Settings",
				"enabled": 0,
				"connection_status": "Disconnected",
				"conduit_base_url": base,
			}
		)
		doc.insert(ignore_permissions=True)
	else:
		doc = frappe.get_single("RareMachine Settings")
		# Only seed URL when empty or still a free-text leftover; never fight Active pairs.
		if not doc.conduit_base_url:
			doc.conduit_base_url = base
			doc.save(ignore_permissions=True)
	# Manual commit: install/migrate lifecycle, outside a request transaction.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _ensure_workspace():
	"""Public Workspace so System Managers find RareMachine in the desk sidebar."""
	if frappe.db.exists("Workspace", "RareMachine"):
		return

	ws = frappe.get_doc(
		{
			"doctype": "Workspace",
			"name": "RareMachine",
			"label": "RareMachine",
			"title": "RareMachine",
			# Frappe v16 Workspace requires type
			"type": "Workspace",
			"module": "RareMachines",
			"public": 1,
			"is_hidden": 0,
			"icon": "integration",
			"content": '[{"id":"raremachines_hdr","type":"header","data":{"text":"<span class=\\"h4\\"><b>RareMachine</b></span>","col":12}},{"id":"raremachines_sc","type":"shortcut","data":{"shortcut_name":"RareMachine Settings","col":4}}]',
			"shortcuts": [
				{
					"type": "DocType",
					"label": "RareMachine Settings",
					"link_to": "RareMachine Settings",
					"doc_view": "",
					"color": "Blue",
				}
			],
		}
	)
	ws.insert(ignore_permissions=True)
	# Manual commit: install/migrate lifecycle, outside a request transaction.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _link_frappe_crm_workspace():
	"""Add a RareMachine shortcut under Frappe CRM workspace when CRM is installed.

	Does not modify CRM app files — only the site DB workspace row.
	"""
	if not frappe.db.exists("Workspace", "Frappe CRM"):
		return
	if not frappe.db.exists("DocType", "RareMachine Settings"):
		return

	# Avoid full Workspace.save() on legacy CRM workspaces that may miss
	# newer mandatory fields (e.g. type). Insert the child row directly.
	exists = frappe.db.exists(
		"Workspace Shortcut",
		{"parent": "Frappe CRM", "link_to": "RareMachine Settings"},
	) or frappe.db.exists(
		"Workspace Shortcut",
		{"parent": "Frappe CRM", "label": "RareMachine"},
	)
	if exists:
		return

	max_idx = frappe.db.sql(
		"""
		select coalesce(max(idx), 0) from `tabWorkspace Shortcut`
		where parent=%s
		""",
		("Frappe CRM",),
	)[0][0]

	row = frappe.get_doc(
		{
			"doctype": "Workspace Shortcut",
			"parent": "Frappe CRM",
			"parenttype": "Workspace",
			"parentfield": "shortcuts",
			"idx": int(max_idx) + 1,
			"type": "DocType",
			"label": "RareMachine",
			"link_to": "RareMachine Settings",
			"color": "Blue",
		}
	)
	row.insert(ignore_permissions=True)

	# Best-effort content JSON so the card appears on the workspace grid.
	try:
		import json

		content_raw = frappe.db.get_value("Workspace", "Frappe CRM", "content") or "[]"
		content = json.loads(content_raw)
		already = any(
			(c.get("type") == "shortcut" and (c.get("data") or {}).get("shortcut_name") == "RareMachine")
			for c in content
		)
		if not already:
			content.append(
				{
					"id": "conduit_crm_sc",
					"type": "shortcut",
					"data": {"shortcut_name": "RareMachine", "col": 3},
				}
			)
			frappe.db.set_value(
				"Workspace",
				"Frappe CRM",
				"content",
				json.dumps(content),
				update_modified=False,
			)
	except Exception:
		# Best-effort: the shortcut ROW above is what makes the link work, and it
		# already succeeded. This only adds the card to the workspace grid, so a
		# failure here is cosmetic and must not abort install or migrate.
		#
		# Logged, not swallowed: without this the card silently fails to appear
		# and there is nothing for support to look at. `uninstall.py` prunes the
		# same entry by `shortcut_name`, so a missing entry costs nothing there.
		frappe.log_error(title="raremachines: could not add shortcut to CRM workspace")

	# Manual commit: install/migrate lifecycle, outside a request transaction.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
