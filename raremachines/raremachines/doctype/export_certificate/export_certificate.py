# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ExportCertificate(Document):
	"""Admin-editable accreditation list offered to an export-market WhatsApp
	lead. Read by RareMachine's `list_export_certificates` endpoint
	(raremachines/api/connect.py) — adding/renaming a row here takes effect
	immediately, no redeploy, no code change."""
