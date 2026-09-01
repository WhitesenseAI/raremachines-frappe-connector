# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class RareMachineSettings(Document):
	def before_save(self):
		# Never persist legacy secret fields if present on older rows.
		if hasattr(self, "api_key"):
			self.api_key = None
