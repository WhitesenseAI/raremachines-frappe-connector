# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""
One-time, CLIENT-SPECIFIC setup for Nest Healthcare's guided WhatsApp intake —
NOT part of `install.py`'s `after_install`/`after_migrate` hooks. Those hooks
run on every site that installs the shared `raremachines` app, across every
client; seeding Nest's own certificate list and brochure email copy there
would leak Nest-specific business content into every future client's site.

Run once, by hand, against Nest Healthcare's site only:

    bench --site <nest-site> execute raremachines.setup_nest_healthcare.setup

Safe to re-run — every insert is guarded by `frappe.db.exists`.

Does NOT create `WhatsApp Templates`/`WhatsApp Notification` rows for the
rep-initiated WhatsApp send — `WhatsAppTemplates.after_insert()`
(frappe_whatsapp's own controller) makes a real, live call to register the
template with Meta's Graph API the moment a `WhatsApp Templates` record is
inserted, so that step has to happen for real, by a human, via Desk, once
a real, Meta-approved template exists (a single shared `nest_brochure`
template with a `{{1}}` market-type variable, or two separate ones — see
the WhatsApp relay plan's Part A2). This module only does the parts that
have no external side effect: the certificate list and the two email
`Notification` rows.
"""

from __future__ import annotations

import frappe

EXPORT_CERTIFICATES = ("WHO-GMP", "Cambodia", "Afghanistan")


def setup():
	if not frappe.db.get_single_value("RareMachine Settings", "whatsapp_intake_enabled"):
		frappe.throw(
			"Enable RareMachine Settings.whatsapp_intake_enabled first — "
			"the Lead fields and Export Certificate doctype this script relies "
			"on don't exist until that flag is on."
		)

	_ensure_export_certificates()
	_ensure_email_notification(
		name="Nest — Domestic Brochure Email",
		condition='doc.brochure_type == "Domestic" and not doc.email_brochure_sent',
		subject="Your Domestic Market brochure from Nest Healthcare",
		message=(
			"<p>Hi {{ doc.first_name }},</p>"
			"<p>Thank you for your interest in Nest Healthcare's Domestic Market products. "
			"Please find our brochure attached.</p>"
			"<p>Our team will be in touch shortly.</p>"
		),
	)
	_ensure_email_notification(
		name="Nest — Export Brochure Email",
		condition='doc.brochure_type == "Export" and not doc.email_brochure_sent',
		subject="Your Export Market brochure from Nest Healthcare",
		message=(
			"<p>Hi {{ doc.first_name }},</p>"
			"<p>Thank you for your interest in Nest Healthcare's Export Market products. "
			"Please find our brochure attached"
			"{% if doc.certificate %}, along with details on our {{ doc.certificate }} accreditation{% endif %}.</p>"
			"<p>Our team will be in touch shortly.</p>"
		),
	)
	# Manual commit: one-time setup script, outside a request transaction.
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	print("Nest Healthcare WhatsApp intake seed data ready.")


def _ensure_export_certificates():
	for cert_name in EXPORT_CERTIFICATES:
		if not frappe.db.exists("Export Certificate", cert_name):
			frappe.get_doc(
				{"doctype": "Export Certificate", "certificate_name": cert_name, "enabled": 1}
			).insert(ignore_permissions=True)


def _ensure_email_notification(*, name: str, condition: str, subject: str, message: str):
	if frappe.db.exists("Notification", name):
		return
	frappe.get_doc(
		{
			"doctype": "Notification",
			"name": name,
			"enabled": 1,
			"channel": "Email",
			"document_type": "CRM Lead",
			"event": "Save",
			"condition": condition,
			"subject": subject,
			"message": message,
			"attach_files": "From Field",
			"from_attach_field": "brochure_pdf",
			"set_property_after_alert": "email_brochure_sent",
			"property_value": "1",
			"recipients": [{"receiver_by_document_field": "email"}],
		}
	).insert(ignore_permissions=True)


def create_whatsapp_notification(
	*, name: str, condition: str, template: str, fields: list[str] | None = None
):
	"""Run by hand once `template` (a real, Meta-approved `WhatsApp
	Templates` name) actually exists — see this module's docstring.

	`fields` maps Lead fields to the template body's `{{1}}`, `{{2}}`, ...
	variables IN ORDER (`WhatsApp Notification.fields`, a child table of
	plain `field_name` rows) — e.g. `fields=["brochure_type"]` for a single
	shared template whose body reads "...our {{1}} Market brochure.",
	filled with the Lead's own `Domestic`/`Export` value at send time. Omit
	for a template with no body variables at all.
	"""
	if frappe.db.exists("WhatsApp Notification", name):
		return
	frappe.get_doc(
		{
			"doctype": "WhatsApp Notification",
			"notification_name": name,
			"notification_type": "DocType Event",
			"reference_doctype": "CRM Lead",
			"doctype_event": "After Save",
			"condition": condition,
			"template": template,
			"field_name": "mobile_no",
			"custom_attachment": 1,
			"attach_from_field": "brochure_pdf",
			"file_name": "brochure.pdf",
			"set_property_after_alert": "whatsapp_brochure_sent",
			"property_value": "1",
			"fields": [{"field_name": f} for f in (fields or [])],
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
