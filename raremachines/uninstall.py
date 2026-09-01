# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""
Tear down everything `install.py` and the pairing flow created on this site.

Frappe removes an app's own doctypes, module and workspace on
`bench uninstall-app`. It does NOT remove rows this app created in OTHER
doctypes, and that is the whole problem this module exists to solve:

  - the **OAuth Client** minted at pair time (`api/connect.py::_ensure_oauth_client`)
    holds a live `client_id`/`client_secret` and a redirect URI pointing at
    RareMachine. Left behind, an "uninstalled" app still has working credentials
    against the customer's site.
  - every **OAuth Bearer Token** and **OAuth Authorization Code** issued against
    that client stays Active, so each user remains authorized to a third party
    they believe they just removed.
  - the **Workspace Shortcut** row and `content` JSON entry inserted into the
    *Frappe CRM* workspace (`install.py::_link_frappe_crm_workspace`) leave a
    dangling "RareMachine" card that 404s.

Ordering matters: revoke the tokens BEFORE deleting the client, so a token is
never orphaned from the client that would have let us find it.

Every step is independently guarded. A failure to clean one artifact must not
abort the uninstall and strand the rest — a partially cleaned site is still
better than a refused uninstall, and each failure is logged for support.
"""

from __future__ import annotations

import json

import frappe

from raremachines.api.connect import CONDUIT_OAUTH_APP_NAME

LOGGER = frappe.logger("raremachines", allow_site=True, file_count=2)


def before_uninstall() -> None:
	"""Registered in hooks.py. Runs while our doctypes still exist."""
	_revoke_oauth_tokens()
	_delete_oauth_client()
	_unlink_frappe_crm_workspace()
	# Manual commit: uninstall must durably remove credentials before the app is torn down.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _raremachines_oauth_client_names() -> list[str]:
	"""Every OAuth Client this app could have created.

	Matched on `app_name`, the same key `_ensure_oauth_client` uses to find or
	create it. Returns a list rather than a single name so a site that somehow
	accumulated more than one is fully cleaned instead of partially.
	"""
	try:
		return frappe.get_all(
			"OAuth Client",
			filters={"app_name": CONDUIT_OAUTH_APP_NAME},
			pluck="name",
		)
	except Exception:
		LOGGER.info("uninstall_oauth_client_lookup_failed")
		return []


def _revoke_oauth_tokens() -> None:
	"""Delete bearer tokens and authorization codes issued to our client.

	Deleted rather than marked revoked: the client row is about to disappear, so
	a "Revoked" token pointing at a nonexistent client is just orphaned data on
	the customer's site.
	"""
	clients = _raremachines_oauth_client_names()
	if not clients:
		return

	for doctype in ("OAuth Bearer Token", "OAuth Authorization Code"):
		if not frappe.db.exists("DocType", doctype):
			continue
		for client in clients:
			try:
				rows = frappe.get_all(doctype, filters={"client": client}, pluck="name")
				for row in rows:
					frappe.delete_doc(
						doctype, row, force=True, ignore_permissions=True, delete_permanently=True
					)
				if rows:
					LOGGER.info("uninstall_revoked doctype=%s count=%s", doctype, len(rows))
			except Exception:
				LOGGER.info("uninstall_revoke_failed doctype=%s", doctype)
				frappe.log_error(title=f"raremachines: uninstall could not revoke {doctype}")


def _delete_oauth_client() -> None:
	"""Delete the OAuth Client itself, so no live credential survives uninstall."""
	for client in _raremachines_oauth_client_names():
		try:
			frappe.delete_doc(
				"OAuth Client", client, force=True, ignore_permissions=True, delete_permanently=True
			)
			LOGGER.info("uninstall_oauth_client_deleted")
		except Exception:
			LOGGER.info("uninstall_oauth_client_delete_failed")
			frappe.log_error(title="raremachines: uninstall could not delete OAuth Client")


def _unlink_frappe_crm_workspace() -> None:
	"""Reverse `install.py::_link_frappe_crm_workspace`.

	Both halves of that mutation are undone: the `Workspace Shortcut` child row
	and the entry appended to the CRM workspace's `content` JSON. Removing only
	the row would leave the grid rendering a card for a shortcut that no longer
	exists.
	"""
	if not frappe.db.exists("Workspace", "Frappe CRM"):
		return

	try:
		rows = frappe.get_all(
			"Workspace Shortcut",
			filters={"parent": "Frappe CRM", "link_to": "RareMachine Settings"},
			pluck="name",
		)
		rows += frappe.get_all(
			"Workspace Shortcut",
			filters={"parent": "Frappe CRM", "label": "RareMachine"},
			pluck="name",
		)
		for row in set(rows):
			frappe.delete_doc(
				"Workspace Shortcut", row, force=True, ignore_permissions=True, delete_permanently=True
			)
	except Exception:
		LOGGER.info("uninstall_crm_shortcut_delete_failed")
		frappe.log_error(title="raremachines: uninstall could not remove CRM shortcut")

	# `update_modified=False` mirrors install.py — this is a repair of another
	# app's row, and bumping its modified stamp would misattribute the change.
	try:
		content_raw = frappe.db.get_value("Workspace", "Frappe CRM", "content") or "[]"
		content = json.loads(content_raw)
		pruned = [
			c
			for c in content
			if not (
				c.get("type") == "shortcut" and (c.get("data") or {}).get("shortcut_name") == "RareMachine"
			)
		]
		if len(pruned) != len(content):
			frappe.db.set_value(
				"Workspace",
				"Frappe CRM",
				"content",
				json.dumps(pruned),
				update_modified=False,
			)
			LOGGER.info("uninstall_crm_content_pruned")
	except Exception:
		LOGGER.info("uninstall_crm_content_prune_failed")
		frappe.log_error(title="raremachines: uninstall could not prune CRM workspace content")
