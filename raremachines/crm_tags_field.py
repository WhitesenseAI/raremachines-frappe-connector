# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""
The RareMachine Tags field on CRM Lead.

RareMachines writes a lead's workspace tags into ONE plain text field that this
app owns end to end, the same model its HubSpot integration uses (an
admin-visible field the integration writes), rather than into Frappe's native
Tag / Tag Link records. One field we own means the full tag set is written on
every sync, so a tag removed in RareMachines disappears here too and nothing a
rep typed elsewhere is ever touched; it is an ordinary field every Frappe
surface can show and filter on; and it needs no Tag-doctype permission from a
Sales User's token.

Who creates it, and when
------------------------
This app, at `after_install` and `after_migrate` — the framework's own mechanism
for an app that needs a field on a doctype it does not own (Frappe CRM itself
uses `create_custom_fields` the same way). Installing the app, or updating it and
running `bench migrate`, is therefore the whole setup: no admin has to click
anything, and no RareMachines user's token ever needs System Manager rights to
alter this site's schema. RareMachines still checks the field against the live
schema before every use and has an explicit admin "Set up tags field" action for
a site that runs an older version of this app.

Frappe CRM only renders a saved "Fields Layout" in its side panel, so a field
that merely exists is invisible there. When the field is created here it is also
appended to the visible part of the Lead Side Panel layout. That happens ONLY on
creation, never on later migrations, so an admin who deliberately removes it from
the layout is not overruled on every update.

`TAGS_FIELDNAME` MUST equal `FRAPPE_TAGS_FIELD` in RareMachines'
`packages/connectors/frappe/constants.ts` — it is the contract between the two.
"""

from __future__ import annotations

import json
from typing import Any

TAGS_DOCTYPE = "CRM Lead"
# `custom_` is Frappe's own prefix for Custom Fieldnames, so this can never
# collide with a standard CRM Lead field.
TAGS_FIELDNAME = "custom_raremachine_tags"
SIDE_PANEL_LAYOUT = "CRM Lead-Side Panel"

TAGS_FIELD: dict[str, Any] = {
	"fieldname": TAGS_FIELDNAME,
	"label": "RareMachine Tags",
	"fieldtype": "Small Text",
	# Written only by RareMachines (the full set, replaced on every sync), so a
	# hand edit would be silently overwritten. Frappe enforces `read_only` in the
	# UI only; the API write RareMachines performs is unaffected.
	"read_only": 1,
	"no_copy": 1,
	"description": "Managed by RareMachines: this lead's workspace tags. Edits made here are overwritten on the next sync.",
}


def inject_field_into_layout(layout: list[dict[str, Any]], fieldname: str) -> bool:
	"""Append `fieldname` to the first visible column of a Fields Layout tree.

	`layout` is the parsed `CRM Fields Layout.layout` JSON: a list of sections,
	each with `columns[].fields[]`, or (tabbed layouts) sections nested under a
	top-level entry's `sections`. Mutates `layout` in place. Returns True only if
	the field was added; False when it is already present anywhere in the tree or
	there is no visible column to put it in. Hidden sections are skipped — a field
	placed in one would not be visible, which defeats the point.
	"""
	for column in _columns(layout):
		if fieldname in (column.get("fields") or []):
			return False

	target = _first_visible_column(layout)
	if target is None:
		return False
	target.setdefault("fields", []).append(fieldname)
	return True


def remove_field_from_layout(layout: list[dict[str, Any]], fieldname: str) -> bool:
	"""Remove every occurrence of `fieldname`; True if anything changed."""
	changed = False
	for column in _columns(layout):
		fields = column.get("fields") or []
		if fieldname in fields:
			column["fields"] = [f for f in fields if f != fieldname]
			changed = True
	return changed


def _columns(layout: list[dict[str, Any]]):
	for section in layout:
		yield from section.get("columns") or []
		for nested in section.get("sections") or []:
			yield from nested.get("columns") or []


def _first_visible_column(layout: list[dict[str, Any]]) -> dict[str, Any] | None:
	for section in layout:
		if section.get("hidden"):
			continue
		columns = section.get("columns")
		if columns:
			return columns[0]
		for nested in section.get("sections") or []:
			if not nested.get("hidden") and nested.get("columns"):
				return nested["columns"][0]
	return None


def ensure_tags_field() -> bool:
	"""Create the field on CRM Lead if it is missing. Idempotent. True if created.

	Existing fields are left exactly as they are (`update=False`), so a label or
	layout an admin customised is never reset by a migrate.
	"""
	import frappe
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	if not frappe.db.exists("DocType", TAGS_DOCTYPE):
		return False
	if frappe.db.exists("Custom Field", {"dt": TAGS_DOCTYPE, "fieldname": TAGS_FIELDNAME}):
		return False

	create_custom_fields({TAGS_DOCTYPE: [TAGS_FIELD]}, update=False)
	_add_to_side_panel()
	return True


def _add_to_side_panel() -> None:
	import frappe

	if not frappe.db.exists("CRM Fields Layout", SIDE_PANEL_LAYOUT):
		return
	doc = frappe.get_doc("CRM Fields Layout", SIDE_PANEL_LAYOUT)
	try:
		layout = json.loads(doc.layout or "[]")
	except ValueError:
		# A layout we cannot parse is not ours to rewrite.
		return
	if inject_field_into_layout(layout, TAGS_FIELDNAME):
		doc.layout = json.dumps(layout)
		doc.save(ignore_permissions=True)


def remove_tags_field() -> None:
	"""Reverse `ensure_tags_field` for uninstall: unlist it from the side panel and
	delete the Custom Field. The field only ever holds RareMachines' own synced tag
	list, so nothing a customer entered is lost."""
	import frappe

	if frappe.db.exists("CRM Fields Layout", SIDE_PANEL_LAYOUT):
		doc = frappe.get_doc("CRM Fields Layout", SIDE_PANEL_LAYOUT)
		try:
			layout = json.loads(doc.layout or "[]")
		except ValueError:
			layout = None
		if layout is not None and remove_field_from_layout(layout, TAGS_FIELDNAME):
			doc.layout = json.dumps(layout)
			doc.save(ignore_permissions=True)

	name = frappe.db.exists("Custom Field", {"dt": TAGS_DOCTYPE, "fieldname": TAGS_FIELDNAME})
	if name:
		frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True, delete_permanently=True)
