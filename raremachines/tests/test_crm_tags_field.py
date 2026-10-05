# Copyright (c) 2026, Whitefield Labs and contributors
# For license information, please see license.txt
"""
Tests for the RareMachine Tags field on CRM Lead (`raremachines/crm_tags_field.py`).

Two layers:

  - `TestLayoutHelpers` is pure (no site, no database): how the field is placed
    into, and removed from, a Frappe CRM "Fields Layout". It runs against the
    layout JSON Frappe CRM itself ships for a Lead side panel, so a change in
    CRM's default shape shows up here.
  - `TestTagsFieldLifecycle` runs on a real site with Frappe CRM installed (CI
    builds one): install provisions the field, migrate is idempotent, uninstall
    cleanup removes it, and the field's spec matches what RareMachines relies on.

Run: `bench --site <site> run-tests --app raremachines`
"""

from __future__ import annotations

import copy
import json
import re
import unittest

from raremachines.crm_tags_field import (
	SIDE_PANEL_LAYOUT,
	TAGS_DOCTYPE,
	TAGS_FIELD,
	TAGS_FIELDNAME,
	ensure_tags_field,
	inject_field_into_layout,
	remove_field_from_layout,
	remove_tags_field,
)

# Verbatim from frappe/crm `crm/install.py::add_default_fields_layout`.
CRM_DEFAULT_LEAD_SIDE_PANEL = json.loads(
	'[{"label": "Details", "name": "details_section", "opened": true, "columns": [{"name": "column_kl92", '
	'"fields": ["organization", "company_description", "website", "territory", "industry", "no_of_employees", '
	'"job_title", "source", "lead_owner", "linkedin", "twitter", "facebook"]}]}, '
	'{"label": "Person", "name": "person_section", "opened": true, "columns": [{"name": "column_XmW2", '
	'"fields": ["salutation", "first_name", "last_name", "email", "mobile_no"]}]}]'
)


def _all_fields(layout):
	out = []
	for section in layout:
		for column in section.get("columns") or []:
			out.extend(column.get("fields") or [])
		for nested in section.get("sections") or []:
			for column in nested.get("columns") or []:
				out.extend(column.get("fields") or [])
	return out


class TestFieldContract(unittest.TestCase):
	def test_fieldname_is_the_contract_with_raremachines(self):
		# RareMachines' `FRAPPE_TAGS_FIELD` (packages/connectors/frappe/constants.ts).
		self.assertEqual(TAGS_FIELDNAME, "custom_raremachine_tags")
		# Frappe's own convention for Custom Fieldnames, so it cannot collide with a standard field.
		self.assertTrue(TAGS_FIELDNAME.startswith("custom_"))
		self.assertRegex(TAGS_FIELDNAME, r"^[a-z][a-z0-9_]*$")

	def test_spec_is_a_read_only_plain_text_field(self):
		self.assertEqual(TAGS_FIELD["fieldname"], TAGS_FIELDNAME)
		self.assertEqual(TAGS_FIELD["fieldtype"], "Small Text")
		# Overwritten on every sync, so a hand edit would be lost.
		self.assertEqual(TAGS_FIELD["read_only"], 1)
		self.assertEqual(TAGS_FIELD["no_copy"], 1)
		# `insert_after` naming a field that does not exist is a hard Frappe validation error.
		self.assertNotIn("insert_after", TAGS_FIELD)


class TestLayoutHelpers(unittest.TestCase):
	def test_adds_to_the_first_visible_column_of_the_default_lead_side_panel(self):
		layout = copy.deepcopy(CRM_DEFAULT_LEAD_SIDE_PANEL)
		self.assertTrue(inject_field_into_layout(layout, TAGS_FIELDNAME))
		self.assertEqual(layout[0]["columns"][0]["fields"][-1], TAGS_FIELDNAME)
		self.assertEqual(_all_fields(layout).count(TAGS_FIELDNAME), 1)
		# Nothing else moved.
		self.assertEqual(layout[1], CRM_DEFAULT_LEAD_SIDE_PANEL[1])

	def test_is_idempotent(self):
		layout = copy.deepcopy(CRM_DEFAULT_LEAD_SIDE_PANEL)
		inject_field_into_layout(layout, TAGS_FIELDNAME)
		snapshot = copy.deepcopy(layout)
		self.assertFalse(inject_field_into_layout(layout, TAGS_FIELDNAME))
		self.assertEqual(layout, snapshot)

	def test_never_duplicates_a_field_an_admin_already_placed_anywhere(self):
		layout = copy.deepcopy(CRM_DEFAULT_LEAD_SIDE_PANEL)
		layout[1]["columns"][0]["fields"].append(TAGS_FIELDNAME)
		self.assertFalse(inject_field_into_layout(layout, TAGS_FIELDNAME))
		self.assertEqual(_all_fields(layout).count(TAGS_FIELDNAME), 1)

	def test_skips_hidden_sections_so_the_field_is_actually_visible(self):
		layout = [
			{"name": "hidden_section", "hidden": True, "columns": [{"name": "c1", "fields": ["a"]}]},
			{"name": "visible_section", "columns": [{"name": "c2", "fields": ["b"]}]},
		]
		self.assertTrue(inject_field_into_layout(layout, TAGS_FIELDNAME))
		self.assertEqual(layout[0]["columns"][0]["fields"], ["a"])
		self.assertEqual(layout[1]["columns"][0]["fields"], ["b", TAGS_FIELDNAME])

	def test_handles_tabbed_layouts_where_sections_nest_under_a_tab(self):
		layout = [
			{
				"name": "first_tab",
				"sections": [
					{"name": "hidden", "hidden": True, "columns": [{"name": "c1", "fields": []}]},
					{"name": "details", "columns": [{"name": "c2", "fields": ["x"]}]},
				],
			}
		]
		self.assertTrue(inject_field_into_layout(layout, TAGS_FIELDNAME))
		self.assertEqual(layout[0]["sections"][1]["columns"][0]["fields"], ["x", TAGS_FIELDNAME])
		self.assertEqual(_all_fields(layout).count(TAGS_FIELDNAME), 1)

	def test_leaves_a_layout_with_no_visible_column_untouched(self):
		for layout in (
			[],
			[{"name": "s", "hidden": True, "columns": [{"name": "c", "fields": []}]}],
			[{"name": "s"}],
		):
			snapshot = copy.deepcopy(layout)
			self.assertFalse(inject_field_into_layout(layout, TAGS_FIELDNAME))
			self.assertEqual(layout, snapshot)

	def test_tolerates_a_column_with_no_fields_key(self):
		layout = [{"name": "s", "columns": [{"name": "c"}]}]
		self.assertTrue(inject_field_into_layout(layout, TAGS_FIELDNAME))
		self.assertEqual(layout[0]["columns"][0]["fields"], [TAGS_FIELDNAME])

	def test_remove_takes_out_every_occurrence_and_only_that_field(self):
		layout = copy.deepcopy(CRM_DEFAULT_LEAD_SIDE_PANEL)
		inject_field_into_layout(layout, TAGS_FIELDNAME)
		layout[1]["columns"][0]["fields"].append(TAGS_FIELDNAME)
		self.assertTrue(remove_field_from_layout(layout, TAGS_FIELDNAME))
		self.assertEqual(layout, CRM_DEFAULT_LEAD_SIDE_PANEL)
		self.assertFalse(remove_field_from_layout(layout, TAGS_FIELDNAME))


class TestTagsFieldLifecycle(unittest.TestCase):
	"""Needs a site with Frappe CRM (CI installs it before this app)."""

	def setUp(self):
		import frappe

		if not frappe.db.exists("DocType", TAGS_DOCTYPE):
			self.skipTest("Frappe CRM is not installed on this site")

	@staticmethod
	def _custom_field_name():
		import frappe

		return frappe.db.exists("Custom Field", {"dt": TAGS_DOCTYPE, "fieldname": TAGS_FIELDNAME})

	@staticmethod
	def _side_panel_fields():
		import frappe

		return _all_fields(
			json.loads(frappe.db.get_value("CRM Fields Layout", SIDE_PANEL_LAYOUT, "layout") or "[]")
		)

	def test_install_provisioned_the_field_with_the_expected_spec(self):
		import frappe

		self.assertTrue(self._custom_field_name(), "after_install must create the tags field")
		field = frappe.get_meta(TAGS_DOCTYPE, cached=False).get_field(TAGS_FIELDNAME)
		self.assertIsNotNone(field)
		self.assertEqual(field.fieldtype, "Small Text")
		self.assertTrue(field.read_only)

	def test_field_is_listed_in_the_lead_side_panel_exactly_once(self):
		import frappe

		if not frappe.db.exists("CRM Fields Layout", SIDE_PANEL_LAYOUT):
			self.skipTest("this CRM version has no Lead side panel layout")
		self.assertEqual(self._side_panel_fields().count(TAGS_FIELDNAME), 1)

	def test_ensure_is_idempotent_and_does_not_fight_an_admin(self):
		import frappe

		self.assertFalse(ensure_tags_field(), "an existing field is not re-created")
		if frappe.db.exists("CRM Fields Layout", SIDE_PANEL_LAYOUT):
			before = frappe.db.get_value("CRM Fields Layout", SIDE_PANEL_LAYOUT, "layout")
			ensure_tags_field()
			self.assertEqual(frappe.db.get_value("CRM Fields Layout", SIDE_PANEL_LAYOUT, "layout"), before)

	def test_remove_then_ensure_round_trips(self):
		"""The uninstall cleanup removes field + side panel entry; a later migrate restores both."""
		import frappe

		try:
			remove_tags_field()
			frappe.clear_cache(doctype=TAGS_DOCTYPE)
			self.assertFalse(self._custom_field_name())
			if frappe.db.exists("CRM Fields Layout", SIDE_PANEL_LAYOUT):
				self.assertNotIn(TAGS_FIELDNAME, self._side_panel_fields())
		finally:
			# Always leave the site as install left it, whatever the assertions above did.
			created = ensure_tags_field()
		self.assertTrue(created)
		self.assertTrue(self._custom_field_name())
		if frappe.db.exists("CRM Fields Layout", SIDE_PANEL_LAYOUT):
			self.assertEqual(self._side_panel_fields().count(TAGS_FIELDNAME), 1)


if __name__ == "__main__":
	unittest.main()
