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


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def sync_export_certificates() -> dict[str, Any]:
	"""Create/enable `Export Certificate` rows from Conduit's per-workspace
	intake config. Idempotent — safe on every `/ops` save.

	Body: `{"certificates": ["Name One", "Name Two", ...]}`.

	Never deletes. A name removed from Conduit's list and re-synced is simply
	left alone (and left enabled if it already was) — disabling/removing a
	certificate is a deliberate Desk action, not an implicit sync side
	effect. Names that already exist are re-enabled if they were off.
	"""
	_verify_install_signature()

	if not frappe.db.exists("DocType", "Export Certificate"):
		frappe.throw(_("Export Certificate DocType is not installed on this site."), frappe.ValidationError)
	if not frappe.db.get_single_value("RareMachine Settings", "whatsapp_intake_enabled"):
		frappe.throw(_("WhatsApp guided intake is not enabled on this site."), frappe.ValidationError)

	raw = frappe.form_dict.get("certificates") or []
	if not isinstance(raw, list):
		frappe.throw(_("certificates must be a list."), frappe.ValidationError)

	names: list[str] = []
	seen: set[str] = set()
	for item in raw:
		name = (item if isinstance(item, str) else "").strip()
		if not name:
			continue
		if len(name) > 140:
			frappe.throw(_("Certificate name too long: {0}").format(name[:40]), frappe.ValidationError)
		if name in seen:
			continue
		seen.add(name)
		names.append(name)

	created = 0
	enabled = 0
	for name in names:
		if frappe.db.exists("Export Certificate", name):
			doc = frappe.get_doc("Export Certificate", name)
			if not doc.enabled:
				doc.enabled = 1
				doc.save(ignore_permissions=True)
				enabled += 1
		else:
			frappe.get_doc({"doctype": "Export Certificate", "certificate_name": name, "enabled": 1}).insert(
				ignore_permissions=True
			)
			created += 1

	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return {"created": created, "enabled": enabled, "total": len(names)}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep: guest-whitelisted-method
def sync_whatsapp_account() -> dict[str, Any]:
	"""Upsert a default `WhatsApp Account` from Conduit's Meta Cloud API
	credentials so Desk WhatsApp + Incoming Message validate() have an
	account to attach — Conduit remains the webhook owner; this only
	mirrors credentials Frappe needs for its own DocType hooks / Desk send.

	Body: `{phoneId, accessToken, businessId?, appId?, verifyToken?,
	url?, version?}`. Marks the row default incoming + outgoing.
	"""
	_verify_install_signature()

	if "frappe_whatsapp" not in frappe.get_installed_apps():
		frappe.throw(_("This site does not have frappe_whatsapp installed."), frappe.ValidationError)

	phone_id = (frappe.form_dict.get("phoneId") or "").strip()
	access_token = (frappe.form_dict.get("accessToken") or "").strip()
	if not phone_id or not access_token:
		frappe.throw(_("phoneId and accessToken are required."), frappe.ValidationError)

	business_id = (frappe.form_dict.get("businessId") or "").strip()
	app_id = (frappe.form_dict.get("appId") or "").strip()
	verify_token = (frappe.form_dict.get("verifyToken") or "").strip()
	url = (frappe.form_dict.get("url") or "https://graph.facebook.com").strip()
	version = (frappe.form_dict.get("version") or "v25.0").strip()

	existing_name = frappe.db.get_value("WhatsApp Account", {"phone_id": phone_id}, "name")
	# Clear other defaults so this Conduit-managed account is the one
	# `get_whatsapp_account()` resolves for both directions.
	frappe.db.sql("UPDATE `tabWhatsApp Account` SET is_default_incoming=0, is_default_outgoing=0")

	if existing_name:
		doc = frappe.get_doc("WhatsApp Account", existing_name)
	else:
		doc = frappe.new_doc("WhatsApp Account")
		doc.phone_id = phone_id
		doc.account_name = f"Conduit {phone_id}"

	doc.url = url
	doc.version = version
	doc.status = "Active"
	doc.is_default_incoming = 1
	doc.is_default_outgoing = 1
	if business_id:
		doc.business_id = business_id
	if app_id:
		doc.app_id = app_id
	if verify_token:
		doc.webhook_verify_token = verify_token
	doc.token = access_token
	doc.save(ignore_permissions=True)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return {"name": doc.name, "phoneId": phone_id}
