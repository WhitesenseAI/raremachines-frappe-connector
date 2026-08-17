import frappe
from frappe.model.rename_doc import rename_doc


def execute():
	"""RareMachines -> RareMachine brand rename left the DocType itself (and
	every site that already had it installed) on the old plural name while
	the controller class, workspace labels and every in-app string moved to
	singular. Without this patch, any site upgrading through that commit has
	a DocType row named "RareMachines Settings" that no longer matches the
	code on disk (which now defines "RareMachine Settings"), so Frappe's
	orphan-doctype cleanup during `bench migrate` deletes it before the
	install hooks that depend on it ever run.
	"""
	if frappe.db.exists("DocType", "RareMachines Settings") and not frappe.db.exists(
		"DocType", "RareMachine Settings"
	):
		rename_doc("DocType", "RareMachines Settings", "RareMachine Settings")
		frappe.reload_doc("raremachines", "doctype", "raremachine_settings")
