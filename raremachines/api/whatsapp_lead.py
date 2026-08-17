"""Fallback lead creation for an unmatched inbound WhatsApp sender.

Gap confirmed by reading `crm.api.whatsapp.validate` in full (2026-08-17): it
only ever does a lookup (`get_contact_lead_or_deal_from_number`) against an
EXISTING Contact. On no match it leaves `reference_doctype`/`reference_name`
`NULL` and does nothing further — no fallback creation exists anywhere in
either `crm` or `frappe_whatsapp` today, so a genuinely new sender's message
is received but never becomes a Lead.

Lives here, in our own app, rather than as a fork of `crm` — same "never
modify a third-party app directly" discipline already used for the
`frappe_whatsapp` fork elsewhere in this plan. Registered on `WhatsApp
Message`'s `validate` event, chained to run AFTER `crm.api.whatsapp.validate`
(apps.txt order: frappe, crm, raremachines — Frappe fires doc_event handlers
in that order), so it only ever acts when CRM's own lookup found nothing.
"""

import frappe

LOGGER = frappe.logger("raremachines", allow_site=True, file_count=2)


def ensure_lead_for_unmatched_sender(doc, method):
    """`validate` hook on WhatsApp Message. No-op unless: Incoming, no CRM
    match already found, and `crm` is actually installed on this site."""
    if doc.type != "Incoming":
        return

    # crm.api.whatsapp.validate already ran (apps.txt order) and either found
    # a match or didn't — only act on "didn't".
    if doc.reference_doctype and doc.reference_name:
        return

    phone_number = doc.get("from")
    if not phone_number:
        return

    if "crm" not in frappe.get_installed_apps():
        return

    # Defensive re-check: crm's own lookup goes through Contact/Contact
    # Phone, not CRM Lead.mobile_no directly, so this guards the (unlikely)
    # case of a Lead existing with this number on its own field but no
    # linked Contact row — avoids creating a second Lead for it.
    existing = frappe.db.exists("CRM Lead", {"mobile_no": phone_number})
    if existing:
        doc.reference_doctype = "CRM Lead"
        doc.reference_name = existing
        return

    lead = frappe.new_doc("CRM Lead")
    lead.update(
        {
            "first_name": doc.get("profile_name") or phone_number,
            "mobile_no": phone_number,
            "status": "New",
        }
    )
    lead.insert(ignore_permissions=True)
    lead.create_contact()

    doc.reference_doctype = "CRM Lead"
    doc.reference_name = lead.name

    LOGGER.info("WhatsApp: auto-created Lead %s for unmatched sender %s", lead.name, phone_number)
