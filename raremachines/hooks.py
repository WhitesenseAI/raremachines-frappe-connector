app_name = "raremachines"
app_title = "RareMachine"
app_publisher = "Whitefield Labs"
app_description = "Operate your Frappe CRM from WhatsApp chat"
app_email = "contact@whitesense.in"
app_license = "gpl-3.0"
app_icon = "octicon octicon-comment-discussion"
app_color = "#2563eb"
source_link = "https://github.com/WhitesenseAI/raremachines-frappe-connector"

# Required listing fields. Both resolve to real pages served by RareMachine
# itself, not placeholders — keep them pointing at live URLs.
app_home = "https://whitesense.in"
support_url = "https://whitesense.in/support"
privacy_policy_url = "https://whitesense.in/privacy"
terms_of_service_url = "https://whitesense.in/terms"
documentation_url = "https://github.com/WhitesenseAI/raremachines-frappe-connector/tree/version-16/docs"

# This app only means anything on a site with Frappe CRM: api/permissions.py
# mirrors CRM Lead/Deal/Organization/Task permissions, and install.py adds a
# shortcut to the Frappe CRM workspace. Without this, installing on a non-CRM
# site half-works silently — the pairing succeeds and then every capability
# resolves to nothing.
required_apps = ["crm"]

after_install = "raremachines.install.after_install"
after_migrate = "raremachines.install.after_migrate"

# Frappe removes this app's own doctypes/module/workspace on uninstall, but
# NOT the OAuth Client minted at pair time, the bearer tokens issued against
# it, or the shortcut install.py inserts into the Frappe CRM workspace. Without
# this hook an "uninstalled" app leaves live credentials on the customer's
# site. See raremachines/uninstall.py.
before_uninstall = "raremachines.uninstall.before_uninstall"

# NOTE: no `before_request` hook, and no patching of `frappe.render_template`.
#
# Showing the signed-in identity on the OAuth consent screen is tempting to
# implement by reassigning `frappe.render_template` at import time. Do not.
# That replaces a core framework function for the WHOLE site — every app's
# templates, not just this one — for the life of the worker, with no way to
# restore it, and it silently drops any keyword argument Frappe adds to that
# signature in a future release. Shipping
# `templates/includes/oauth_confirmation.html` has the same problem by a
# different route: that is the exact path of Frappe's own copy, so it would
# override the consent screen for every OAuth client on the site, including
# other apps'.
#
# Frappe's app authoring guidelines say plainly: "Don't override base
# functionalities provided by Frappe." The identity is surfaced instead by
# `api.connect.oauth_relogin`, which renders it through
# `frappe.respond_as_web_page` — a supported mechanism that touches nothing
# outside this app.

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "raremachines",
# 		"logo": "/assets/raremachines/logo.png",
# 		"title": "RareMachine",
# 		"route": "/raremachines",
# 		"has_permission": "raremachines.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/raremachines/css/raremachines.css"
# app_include_js = "/assets/raremachines/js/raremachines.js"

# include js, css files in header of web template
# web_include_css = "/assets/raremachines/css/raremachines.css"
# web_include_js = "/assets/raremachines/js/raremachines.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "raremachines/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "raremachines/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "raremachines.utils.jinja_methods",
# 	"filters": "raremachines.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "raremachines.install.before_install"
# after_install = "raremachines.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "raremachines.uninstall.before_uninstall"
# after_uninstall = "raremachines.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "raremachines.utils.before_app_install"
# after_app_install = "raremachines.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "raremachines.utils.before_app_uninstall"
# after_app_uninstall = "raremachines.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "raremachines.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "raremachines.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Harmless when `frappe_whatsapp` isn't installed — WhatsApp Message simply
# never exists, and the hook never fires. Chained to run AFTER
# crm.api.whatsapp.validate (apps.txt order: frappe, crm, raremachines), so
# it only acts when CRM's own contact-number lookup found nothing. See
# raremachines/api/whatsapp_lead.py's module doc for the gap this closes.
#
# `after_insert` stamps `last_whatsapp_message_at` on the resolved Lead, for
# triage sorting in the Desk list — see `stamp_lead_last_message_at`'s own
# doc for why it's `after_insert` and not chained onto the `validate` above.
#
# `clean_up_outgoing_attach` runs first — it only ever touches
# `doc.message`/`doc.attach`/the underlying `File` doc, never
# `reference_doctype`/`reference_name`, so its position relative to
# `ensure_lead_for_unmatched_sender` doesn't matter functionally.
#
# `reprivatize_auto_publicized_attach` (also `after_insert`) — position
# relative to `stamp_lead_last_message_at` doesn't matter either; they
# touch disjoint fields (`WhatsApp Message.attach` vs `CRM Lead.
# last_whatsapp_message_at`).
doc_events = {
	"WhatsApp Message": {
		"validate": [
			"raremachines.api.whatsapp_lead.clean_up_outgoing_attach",
			"raremachines.api.whatsapp_lead.ensure_lead_for_unmatched_sender",
		],
		"after_insert": [
			"raremachines.api.whatsapp_lead.stamp_lead_last_message_at",
			"raremachines.api.whatsapp_lead.reprivatize_auto_publicized_attach",
		],
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"raremachines.tasks.all"
# 	],
# 	"daily": [
# 		"raremachines.tasks.daily"
# 	],
# 	"hourly": [
# 		"raremachines.tasks.hourly"
# 	],
# 	"weekly": [
# 		"raremachines.tasks.weekly"
# 	],
# 	"monthly": [
# 		"raremachines.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "raremachines.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "raremachines.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "raremachines.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "raremachines.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["raremachines.utils.before_request"]
# after_request = ["raremachines.utils.after_request"]

# Job Events
# ----------
# before_job = ["raremachines.utils.before_job"]
# after_job = ["raremachines.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"raremachines.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
