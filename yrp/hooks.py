app_name = "yrp"
app_title = "YRP"
app_publisher = "Mohammed Anas"
app_description = "Your Resource Planning"
app_email = "mohammedanasman123@gmail.com"
app_license = "mit"

fixtures = [
	{"dt": "Workflow", "filters": [["name", "in", ["Item Price Workflow", "Process Cost Workflow"]]]},
	{"dt": "Property Setter", "filters": [["name", "in", ["Communication-communication_medium-options"]]]},
]

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "yrp",
# 		"logo": "/assets/yrp/logo.png",
# 		"title": "YRP",
# 		"route": "/yrp",
# 		"has_permission": "yrp.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = ["/assets/yrp/css/form_overrides.css"]
app_include_js = ["yrp.bundle.js"]

# include js, css files in header of web template
# web_include_css = "/assets/yrp/css/yrp.css"
# web_include_js = "/assets/yrp/js/yrp.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "yrp/public/scss/website"

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
# app_include_icons = "yrp/public/icons.svg"

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
# 	"methods": "yrp.utils.jinja_methods",
# 	"filters": "yrp.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "yrp.install.before_install"
after_install = "yrp.yrp.doctype.notification_template.notification_template.add_whatsapp_communication_medium"

# Uninstallation
# ------------

# before_uninstall = "yrp.uninstall.before_uninstall"
# after_uninstall = "yrp.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "yrp.utils.before_app_install"
# after_app_install = "yrp.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "yrp.utils.before_app_uninstall"
# after_app_uninstall = "yrp.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "yrp.notifications.get_notification_config"

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

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"yrp.yrp_stock.doctype.stock_integrity_check.stock_integrity_check.run_daily_check",
		"yrp.whatsapp_templates.sync_templates_from_hub",
	],
	"hourly": [
		"yrp.yrp_stock.doctype.repost_item_valuation.repost_item_valuation.repost_entries",
	],
	"cron": {
		"0 1 * * *": [
			"yrp.tasks.daily",
		],
	},
}

doc_events = {
	"YRP Stock Settings": {
		"on_update": "yrp.stock.dimensions.clear_dimension_cache",
	},
	"User": {
		# Per-user UI storage lifecycle (PER_USER_UI_SPEC.md §3.3): the YRP UI
		# Preference docname == user; keep it in sync on offboarding/rename.
		"on_trash": "yrp.yrp.api.ui_config.delete_ui_preference_for_user",
		"before_rename": "yrp.yrp.api.ui_config.merge_ui_preference_for_user",
		"after_rename": "yrp.yrp.api.ui_config.rename_ui_preference_for_user",
	},
}

after_migrate = [
	"yrp.stock.dimensions.create_dimension_fields",
	"yrp.patches.add_sle_composite_index.execute",
	"yrp.yrp.doctype.notification_template.notification_template.add_whatsapp_communication_medium",
]

# Testing
# -------

# before_tests = "yrp.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "yrp.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "yrp.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "yrp.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["yrp.utils.before_request"]
# after_request = ["yrp.utils.after_request"]

# Job Events
# ----------
# before_job = ["yrp.utils.before_job"]
# after_job = ["yrp.utils.after_job"]

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
# 	"yrp.auth.validate"
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
