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

import os
from urllib.parse import unquote

import frappe
import frappe.geo.country_info
from frappe.utils import now_datetime

LOGGER = frappe.logger("raremachines", allow_site=True, file_count=2)


def ensure_lead_for_unmatched_sender(doc, method):
	"""`validate` hook on WhatsApp Message. No-op unless: Incoming, no CRM
	Lead match already found, and `crm` is actually installed on this site.

	**Bug fixed 2026-08-27** — the original guard here was
	`if doc.reference_doctype and doc.reference_name: return`, on the
	assumption `crm.api.whatsapp.validate` (which runs first, apps.txt
	order) either finds a full CRM Lead match or leaves both fields NULL.
	That assumption is wrong: `get_contact_lead_or_deal_from_number` can
	resolve to a bare **Contact** with `reference_doctype="Contact"` when a
	Contact exists but has no linked Lead — which is exactly the state a
	deleted Lead leaves behind (deleting a Lead through the CRM UI does
	NOT delete its linked Contact). Confirmed live: a Lead deleted, then a
	fresh inbound message from the same number, resolved to
	`("<contact-name>", "Contact")` — the old guard treated that as
	"already handled" and silently created no Lead at all, forever, for
	that number. Fixed: only skip when a REAL Lead was found
	(`reference_doctype == "CRM Lead"`); a Contact-only match still falls
	through to Lead creation below, reusing that same Contact (via
	`existing_contact=`) rather than creating a duplicate.
	"""
	if doc.type != "Incoming":
		return

	if doc.reference_doctype == "CRM Lead" and doc.reference_name:
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

	# An orphaned Contact (crm's own lookup already found it — see this
	# function's doc comment) is reused, never duplicated: `create_contact`
	# checks `existing_contact` first and links to it instead of inserting
	# a new one.
	existing_contact = doc.reference_name if doc.reference_doctype == "Contact" else None

	lead = frappe.new_doc("CRM Lead")
	lead.update(
		{
			"first_name": doc.get("profile_name") or phone_number,
			"mobile_no": phone_number,
			"status": "New",
		}
	)
	lead.insert(ignore_permissions=True)
	lead.create_contact(existing_contact=existing_contact)

	doc.reference_doctype = "CRM Lead"
	doc.reference_name = lead.name

	LOGGER.info("WhatsApp: auto-created Lead %s for unmatched sender %s", lead.name, phone_number)


def normalize_lead_mobile_no(doc, method):
	"""`validate` hook on CRM Lead — prepends the tenant's ISD code to a
	bare local-format `mobile_no` before it is ever saved.

	Found live (2026-08-28): a rep created a Lead via the Conduit WhatsApp
	agent by typing the number without a country code ("6263581769", not
	"916263581769"). Conduit correctly wrote exactly what it was given —
	CLAUDE.md's own rule is "validate against the live schema", not
	"reformat a value the model wasn't told to reformat" — but
	`frappe_whatsapp`'s `WhatsAppNotification.format_number()` only ever
	strips a leading `+`; it never adds a country code. The brochure send
	for that Lead came back `status: failed` with no attachment delivered,
	and there is no path in this codebase where the Lead's `mobile_no`
	itself gets corrected afterwards — every future automated send for that
	Lead would keep failing the same way. Fixed at the one place all
	creation paths funnel through (Conduit's `create_contact`, the
	business-card scan, and `ensure_lead_for_unmatched_sender` above all
	end up here), not by trying to normalize the number at every call site
	individually.

	Deliberately conservative: only touches a number that is ALL DIGITS and
	exactly 10 characters long — the unambiguous "bare Indian mobile
	number, no ISD code" shape. Anything already carrying a `+`, an ISD
	code, spaces, dashes, or an unexpected length is left untouched rather
	than guessed at.
	"""
	if not doc.mobile_no:
		return

	digits_only = doc.mobile_no.strip()
	if not digits_only.isdigit() or len(digits_only) != 10:
		return

	country = frappe.db.get_single_value("System Settings", "country") or "India"
	isd = (frappe.geo.country_info.get_country_info(country) or {}).get("isd", "+91")

	doc.mobile_no = f"{isd.lstrip('+')}{digits_only}"


def clean_up_outgoing_attach(doc, method):
	"""`validate` hook on WhatsApp Message — transparently fixes an Outgoing
	send's private `attach` BEFORE `before_insert` reaches
	`frappe_whatsapp`'s own `WhatsAppMessage.send_outgoing()`, which would
	otherwise build a link to it and hand that straight to Meta.

	Confirmed live (2026-08-27): Meta's document-fetch carries no Frappe
	session, so a `/private/files/...` link 403s when Meta tries to
	retrieve it — the send doesn't error immediately, it comes back later
	as a `131026 Message undeliverable` status callback with no indication
	of why.

	Root cause traced to `crm` frontend's own `WhatsAppBox.vue`: its
	`<FileUploader>` passes no `upload-args`, so `frappe-ui`'s component
	hard-defaults every upload from that box to private — there's no
	checkbox, no rep choice, every attachment is private, always.
	Deliberately NOT fixed by patching `crm`'s own Vue source (would only
	live on this one installation, not distributable with `raremachines`)
	and NOT fixed by throwing an error either (the rep has no way to
	un-privatize the file from that same compose box — the error would just
	be a dead end). Fixed here instead, transparently: flip the underlying
	`File` doc public and rewrite `doc.attach` to match, exactly what
	`raremachines.api.connect.get_brochure_pdf_url` already requires be
	true for the automated brochure send — same constraint, applied
	automatically here instead of just checked there.
	"""
	if doc.type != "Outgoing":
		return

	# `crm.api.whatsapp.create_whatsapp_message` does `"message": message or
	# attach` — a rep sending a file with no caption typed ends up with the
	# raw file path/URL as the message. Confirmed live. Fixed here rather
	# than in `crm`'s own endpoint (same "don't fork crm" constraint as
	# above) — replace it with just the filename whenever `message` and
	# `attach` are identical, the unambiguous signal this is that exact
	# fallback and not a rep who genuinely typed a path on purpose.
	if doc.attach and doc.message == doc.attach:
		doc.message = unquote(os.path.basename(doc.attach.split("?")[0]))

	if not doc.attach or not doc.attach.startswith("/private/"):
		return

	file_doc = frappe.get_doc("File", {"file_url": doc.attach})
	file_doc.is_private = 0
	file_doc.save(ignore_permissions=True)
	file_doc.reload()
	doc.attach = file_doc.file_url

	# Read back by `reprivatize_auto_publicized_attach` (`after_insert`,
	# below) — `doc.flags` lives only on this in-memory object, never
	# persisted, so it can't leak into any other request/doc. Storing the
	# File's name (not just a boolean) is what lets that hook re-privatize
	# EXACTLY this file and nothing else — see its own doc comment for why
	# that precision matters (the shared, admin-configured brochure PDF
	# must never be touched here, and never is: it's already public before
	# this function ever runs, so the `/private/` guard above already
	# skips it, and this flag is only ever set for a file THIS call itself
	# just flipped).
	doc.flags.raremachines_auto_publicized_attach = file_doc.name

	LOGGER.info(
		"WhatsApp: auto-publicized private attach %s -> %s for outgoing send",
		file_doc.name,
		file_doc.file_url,
	)


def reprivatize_auto_publicized_attach(doc, method):
	"""`after_insert` hook on WhatsApp Message — re-privatizes a file
	`clean_up_outgoing_attach` (this module's own `validate` hook, moments
	earlier in the same request) auto-published, once the send is
	CONFIRMED to have succeeded — not merely attempted.

	Safe by construction, not by inference: `after_insert` only fires if
	`before_insert` completed without raising, and `frappe_whatsapp`'s own
	`WhatsAppMessage.send_outgoing()` (called from `before_insert`)
	`frappe.throw()`s on ANY send failure — both the template and
	non-template code paths — which unconditionally aborts the whole
	`insert()` before a row is ever written. There is no path to
	`after_insert` for a message Meta rejected or that never actually
	sent; if this hook runs at all, the row exists, which means the send
	did not fail. Never re-privatizes a file whose delivery is unconfirmed.

	Reads `doc.flags` (set moments earlier, same in-memory doc, by
	`clean_up_outgoing_attach`) rather than re-deriving "was this
	auto-published" from `doc.attach` alone — deliberately: a file that
	was ALREADY public before this message existed (the shared brochure
	PDF, reused across every future guided-intake conversation) must never
	be re-privatized just because it happens to be attached to a
	successful send; only a file THIS module itself flipped, in this exact
	request, is eligible.

	Also fixes what re-privatizing would otherwise silently break: moving
	a `File` from public to private physically relocates it on disk
	(`handle_is_private_changed`) and changes its `file_url` — the
	already-saved WhatsApp Message's `attach` field is a plain string
	captured at insert time, so without updating it here it would keep
	pointing at a path that no longer exists, breaking even a logged-in
	rep's own ability to reopen it later from Frappe's WhatsApp panel.
	"""
	published_file = doc.flags.get("raremachines_auto_publicized_attach")
	if not published_file:
		return

	file_doc = frappe.get_doc("File", published_file)
	file_doc.is_private = 1
	file_doc.save(ignore_permissions=True)
	file_doc.reload()

	frappe.db.set_value("WhatsApp Message", doc.name, "attach", file_doc.file_url)

	LOGGER.info("WhatsApp: re-privatized %s after confirmed send", published_file)


def stamp_lead_last_message_at(doc, method):
	"""`after_insert` hook on WhatsApp Message — stamps `CRM Lead.last_whatsapp_message_at`
	for triage sorting/list-view visibility, per PROGRESS.md's global WhatsApp
	visibility task. `after_insert`, not `validate`: `ensure_lead_for_unmatched_sender`
	(also on this doctype, `validate`) must run first to resolve
	`reference_doctype`/`reference_name` for a brand-new sender, and this only
	needs the FINAL, saved values — Frappe fires `validate` before insert and
	`after_insert` once the row actually exists, so ordering is guaranteed
	without listing this after the other hook explicitly.

	`last_whatsapp_message_at` only exists on `CRM Lead` when
	`RareMachine Settings.whatsapp_intake_enabled` is on (see `install.py`'s
	`_ensure_whatsapp_intake_customizations`) — guarded the same way here so
	this is a harmless no-op on every site that hasn't opted in, rather than
	an `set_value` against a column that doesn't exist.
	"""
	if doc.reference_doctype != "CRM Lead" or not doc.reference_name:
		return
	if not frappe.db.get_single_value("RareMachine Settings", "whatsapp_intake_enabled"):
		return

	frappe.db.set_value("CRM Lead", doc.reference_name, "last_whatsapp_message_at", now_datetime())
