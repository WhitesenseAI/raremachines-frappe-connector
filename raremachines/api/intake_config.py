# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""
Conduit -> Frappe push of the WhatsApp guided-intake Lead field schema.

Historically this app's `install.py` hardcoded a Nest-Healthcare-shaped set
of `CRM Lead` fields (`enquiry_type`, `market_type`, `regulated_status`,
`certificate`, ...) behind the single `whatsapp_intake_enabled` toggle, so
EVERY client who opted in got the exact same fields — not client-configurable.
This endpoint is the fix: Conduit's `/ops` admin dashboard now owns the field
list per workspace (`WhatsAppIntakeConfig.config.frappeLeadFields`) and pushes
it here whenever an admin saves it.

Auth: same HMAC-over-`timestamp.body`, keyed by `install_secret`, that every
other RareMachine -> Frappe relay call already uses (`receive_whatsapp_lead`,
`list_export_certificates`, ...) — see `org_users.py`'s
`_verify_install_signature`. Not a new trust boundary.

Never deletes. `create_custom_fields` (Frappe's own idempotent helper) only
creates missing fields and updates existing ones that changed
(label/options/etc). A field removed from Conduit's config and re-synced is
simply no longer touched by future syncs — it is NOT deleted here, since an
existing Lead's data in that field would be destroyed. Removal, if ever
wanted, is a deliberate separate action, not an implicit side effect of a
sync — see this project's CLAUDE.md: "the customer's CRM/ERP is
authoritative... never destroy their data."
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from raremachines.api.org_users import _verify_install_signature

# The only Custom Field types this endpoint will create — a small, deliberate
# subset of Frappe's ~30 field types, matching exactly what Conduit's `/ops`
# form exposes. Anything else is a caller bug (a mistyped fieldtype in a
# hand-edited request), not a shape we should quietly pass through to
# `create_custom_fields` and let Frappe reject with a less helpful error.
ALLOWED_FIELDTYPES = {"Select", "Data", "Check", "Attach", "Small Text", "Column Break", "Section Break"}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def sync_lead_fields() -> dict[str, Any]:
	"""Create/update `CRM Lead` Custom Fields from Conduit's per-workspace
	`frappeLeadFields` config. Idempotent — safe to call on every `/ops`
	save, not just once.

	Body: `{"fields": [{"fieldname", "label", "fieldtype", "options"?}, ...]}`.
	Inserted after the FIXED `whatsapp_intake_column_break` field
	`install.py` already creates, so client fields land in the same
	"WhatsApp Intake" section as the engine's own bookkeeping fields.
	"""
	_verify_install_signature()

	if "crm" not in frappe.get_installed_apps():
		frappe.throw(_("This site does not have the CRM app installed."), frappe.ValidationError)
	if not frappe.db.get_single_value("RareMachine Settings", "whatsapp_intake_enabled"):
		frappe.throw(_("WhatsApp guided intake is not enabled on this site."), frappe.ValidationError)

	raw_fields = frappe.form_dict.get("fields") or []
	if not isinstance(raw_fields, list) or not raw_fields:
		frappe.throw(_("fields must be a non-empty list."), frappe.ValidationError)

	seen_fieldnames: set[str] = set()
	custom_fields: list[dict[str, Any]] = []
	insert_after = "whatsapp_intake_column_break"

	for raw in raw_fields:
		fieldname = (raw.get("fieldname") or "").strip()
		label = (raw.get("label") or "").strip()
		fieldtype = (raw.get("fieldtype") or "").strip()
		options = raw.get("options")

		if not fieldname or not fieldname.replace("_", "").isalnum() or fieldname[0].isdigit():
			frappe.throw(_("Invalid fieldname: {0}").format(fieldname), frappe.ValidationError)
		if fieldname in seen_fieldnames:
			frappe.throw(_("Duplicate fieldname: {0}").format(fieldname), frappe.ValidationError)
		seen_fieldnames.add(fieldname)

		if fieldtype not in ALLOWED_FIELDTYPES:
			frappe.throw(_("Unsupported fieldtype: {0}").format(fieldtype), frappe.ValidationError)
		if not label and fieldtype not in ("Column Break", "Section Break"):
			frappe.throw(_("label is required for field: {0}").format(fieldname), frappe.ValidationError)

		field: dict[str, Any] = {
			"fieldname": fieldname,
			"fieldtype": fieldtype,
			"label": label,
			"insert_after": insert_after,
		}
		if fieldtype == "Select" and options:
			field["options"] = options
		custom_fields.append(field)
		insert_after = fieldname

	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	# `create_custom_fields()` has no `ignore_permissions` knob (unlike the
	# per-doc `insert(ignore_permissions=True)` this app's other guest
	# endpoints use) — it enforces Custom Field doctype permissions against
	# `frappe.session.user`, which is "Guest" here since this endpoint is
	# `allow_guest=True` (authenticated by `_verify_install_signature()`'s
	# HMAC instead of a Desk session). `install.py`'s own identical call
	# works only because migrate/install always runs as Administrator.
	# Scoped elevation for exactly this call — same trust level the HMAC
	# check already establishes (a verified Conduit `/ops` admin action),
	# restored in `finally` so nothing else in this request runs elevated.
	previous_user = frappe.session.user
	try:
		frappe.set_user("Administrator")  # nosemgrep: frappe-setuser
		create_custom_fields({"CRM Lead": custom_fields})
	finally:
		frappe.set_user(previous_user)  # nosemgrep: frappe-setuser

	# Manual commit: this is an admin-triggered, outside-a-normal-request-flow
	# schema change, same discipline `install.py`'s own `create_custom_fields`
	# call uses.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	return {"synced": len(custom_fields)}
