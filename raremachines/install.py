# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe

from raremachines.config.saas import get_conduit_base_url


def after_install():
	_ensure_settings()
	_ensure_workspace()
	_link_frappe_crm_workspace()
	_ensure_whatsapp_intake_customizations()
	_ensure_whatsapp_message_list_visibility()
	_ensure_whatsapp_message_id_unique()


def after_migrate():
	"""Keep nav + defaults healthy after migrate."""
	_ensure_settings()
	_ensure_workspace()
	_link_frappe_crm_workspace()
	_ensure_whatsapp_intake_customizations()
	_ensure_whatsapp_message_list_visibility()
	_ensure_whatsapp_message_id_unique()


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


def _ensure_whatsapp_intake_customizations():
	"""FIXED infra fields the WhatsApp guided-intake flow (Conduit-side)
	always needs on `CRM Lead` (section/column layout, the engine's own
	brochure-send bookkeeping), plus the two brochure-file settings on
	`RareMachine Settings`. Client-specific business fields (what used to be
	hardcoded `enquiry_type`/`market_type`/`regulated_status`/`certificate`
	here) are NOT created by this installer anymore — they're pushed
	per-workspace from Conduit's `/ops` dashboard via `sync_lead_fields`
	(`api/intake_config.py`), so a new client's field shape is config, not a
	code change to this shared app.

	`raremachines` is ONE shared app installed on every client's own,
	separate Frappe site — this function must never assume the site it's
	running on is Nest Healthcare's. So the whole block is gated on
	`RareMachine Settings.whatsapp_intake_enabled` — off by default, an
	explicit per-site opt-in (same manual-allowlist posture as the rest of
	this app's access gating) — rather than always-on for every install.
	The toggle field itself is added unconditionally, since it IS the gate.

	`create_custom_fields` is Frappe's own idempotent helper — safe to call
	on every install/migrate, only creates what's missing. Harmless when
	`crm` isn't installed: skipped entirely, same guard discipline as
	`api/whatsapp_lead.py`'s own `"crm" not in frappe.get_installed_apps()`.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	if not frappe.db.exists("DocType", "RareMachine Settings"):
		return

	create_custom_fields(
		{
			"RareMachine Settings": [
				{
					"fieldname": "whatsapp_intake_enabled",
					"fieldtype": "Check",
					"label": "WhatsApp Guided Intake Enabled",
					"insert_after": "install_secret",
					"description": (
						"Turns on the Domestic/Export guided WhatsApp intake flow for this"
						" site — adds Lead fields, the Export Certificate list, and the"
						" brochure-send config. Off by default; enable deliberately, not"
						" a default every client gets."
					),
				},
			],
		}
	)

	if "crm" not in frappe.get_installed_apps():
		return
	if not frappe.db.get_single_value("RareMachine Settings", "whatsapp_intake_enabled"):
		return

	create_custom_fields(
		{
			"CRM Lead": [
				{
					"fieldname": "whatsapp_intake_section",
					"fieldtype": "Section Break",
					"label": "WhatsApp Intake",
					"insert_after": "source",
					"collapsible": 1,
				},
				# Nest-shaped BUSINESS fields (enquiry_type, market_type,
				# regulated_status) used to be hardcoded here. They are now
				# CLIENT-CONFIGURABLE — pushed per-workspace from Conduit's
				# `/ops` admin dashboard via `sync_lead_fields`
				# (`api/intake_config.py`), not created by this installer. A
				# site with `whatsapp_intake_enabled` on but no sync yet simply
				# has no such fields until an admin configures them — the
				# engine's own field writes are no-ops until then, not errors.
				#
				# `certificate` stays FIXED here, deliberately NOT
				# client-configurable like the others — it's a `Link` to the
				# `Export Certificate` doctype that `api/connect.py`'s
				# `_apply_guided_intake_fields` depends on being exactly that
				# type (`frappe.db.exists("Export Certificate", certificate)`),
				# and `sync_lead_fields`'s own `ALLOWED_FIELDTYPES` doesn't even
				# support `Link` — trying to sync it as a client field collides
				# with this fixed definition ("Fieldtype cannot be changed from
				# Link to Data").
				{
					"fieldname": "whatsapp_intake_column_break",
					"fieldtype": "Column Break",
					"insert_after": "whatsapp_intake_section",
				},
				{
					"fieldname": "certificate",
					"fieldtype": "Link",
					"label": "Certificate",
					"options": "Export Certificate",
					"insert_after": "whatsapp_intake_column_break",
				},
				{
					"fieldname": "brochure_type",
					"fieldtype": "Select",
					"label": "Brochure Type",
					"options": "\nDomestic\nExport",
					"insert_after": "certificate",
					"read_only": 1,
				},
				{
					"fieldname": "brochure_pdf",
					"fieldtype": "Attach",
					"label": "Brochure PDF",
					"insert_after": "brochure_type",
					"read_only": 1,
				},
				{
					"fieldname": "brochure_sent_column_break",
					"fieldtype": "Column Break",
					"insert_after": "brochure_pdf",
				},
				{
					"fieldname": "whatsapp_brochure_sent",
					"fieldtype": "Check",
					"label": "WhatsApp Brochure Sent",
					"insert_after": "brochure_sent_column_break",
					"read_only": 1,
				},
				{
					"fieldname": "email_brochure_sent",
					"fieldtype": "Check",
					"label": "Email Brochure Sent",
					"insert_after": "whatsapp_brochure_sent",
					"read_only": 1,
				},
				{
					"fieldname": "last_whatsapp_message_at",
					"fieldtype": "Datetime",
					"label": "Last WhatsApp Message At",
					"insert_after": "email_brochure_sent",
					"read_only": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
				},
			],
			"RareMachine Settings": [
				{
					"fieldname": "brochure_section",
					"fieldtype": "Section Break",
					"label": "WhatsApp Intake Brochures",
					"insert_after": "install_secret",
					"collapsible": 1,
				},
				{
					"fieldname": "domestic_brochure_file",
					"fieldtype": "Attach",
					"label": "Domestic Brochure PDF",
					"insert_after": "brochure_section",
				},
				{
					"fieldname": "export_brochure_file",
					"fieldtype": "Attach",
					"label": "Export Brochure PDF",
					"insert_after": "domestic_brochure_file",
				},
			],
		}
	)
	# Manual commit: install/migrate lifecycle, outside a request transaction.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _ensure_whatsapp_message_list_visibility():
	"""Curate `/app/whatsapp-message`'s Desk list — out of the box it's every
	standard field in declaration order, useless for triage. Not gated behind
	`whatsapp_intake_enabled`: the raw message log is useful to any client
	running WhatsApp through this app at all, independent of whether they've
	opted into the guided-intake flow.

	`WhatsApp Message` belongs to `frappe_whatsapp`, not this app — customized
	via `Property Setter` (the same mechanism Desk's own "Customize Form" UI
	writes), never by editing that app's doctype JSON directly, same
	never-touch-a-third-party-app discipline as `api/whatsapp_lead.py`.
	`make_property_setter` is a safe upsert (its own `validate()` deletes any
	existing setter for the same doctype/field/property before insert), so
	this is safe to call on every install/migrate.

	Deliberately no `Kanban Board` doc here: unlike this Desk list, Frappe
	CRM's own Lead Kanban groups by a field chosen live from its UI dropdown
	(no server-side doctype record to seed) — `enquiry_type` being
	`in_standard_filter` (see `_ensure_whatsapp_intake_customizations`) is
	what makes it selectable there; there's nothing further to configure
	from this side.
	"""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return

	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	list_view_fields = ("from", "profile_name", "reference_name", "status", "type")
	for fieldname in list_view_fields:
		make_property_setter("WhatsApp Message", fieldname, "in_list_view", "1", "Check")
	for fieldname in ("status", "type", "reference_name"):
		make_property_setter("WhatsApp Message", fieldname, "in_standard_filter", "1", "Check")

	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _ensure_whatsapp_message_id_unique():
	"""`WhatsApp Message.message_id` unique at the DB level — same
	Property-Setter mechanism/discipline as
	`_ensure_whatsapp_message_list_visibility` above (never edit
	`frappe_whatsapp`'s own doctype JSON directly).

	Found in review (2026-08-31): `receive_whatsapp_lead`/
	`receive_whatsapp_status` in `api/connect.py` both do a plain
	`frappe.db.exists(..., message_id)` check followed by a SEPARATE
	`insert()` — a genuine check-then-act race under a truly concurrent
	retry of the same Meta webhook delivery. A DB-level unique constraint
	turns that race into a clean `DuplicateEntryError` those two callers
	already catch and treat as an idempotent no-op, instead of silently
	inserting a second row for the same message. MariaDB/MySQL's unique
	index semantics allow any number of NULLs, so this is safe for every
	pre-existing row that predates this app's idempotency key.
	"""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return

	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	make_property_setter("WhatsApp Message", "message_id", "unique", "1", "Check")
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
