app_name = "yrp"
app_title = "YRP"
app_publisher = "Mohammed Anas"
app_description = "Your Resource Planning"
app_email = "mohammedanasman123@gmail.com"
app_license = "mit"

fixtures = [
	{"dt": "Custom Field", "filters": [["module", "=", "YRP"]]},
	{"dt": "Workflow", "filters": [["name", "in", ["Item Price Workflow", "Process Cost Workflow"]]]},
	{
		"dt": "Workflow State",
		"filters": [["name", "in", ["Draft", "Approval Pending", "Approved", "Rejected", "Expired"]]],
	},
	{
		"dt": "Workflow Action Master",
		"filters": [["name", "in", ["Submit", "Approve", "Reject", "Expired"]]],
	},
	{"dt": "Property Setter", "filters": [["name", "in", ["Communication-communication_medium-options"]]]},
]

# Apps
# ------------------

required_apps = ["erpnext"]

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
doctype_js = {
	"YRP Visit": "public/js/retail_sales_flow.js",
	"YRP Retail Order": "public/js/retail_sales_flow.js",
	"YRP Retail Order Summary": "public/js/retail_sales_flow.js",
	"Delivery Note": "public/js/retail_sales_flow.js",
	"Packing Slip": "public/js/retail_sales_flow.js",

	"Customer": "public/js/partner_contacts.js",
	"Sales Partner": "public/js/partner_contacts.js",
	"Contact": "public/js/partner_contacts.js",
	"Address": "public/js/partner_contacts.js",
	"YRP Partner": "public/js/partner_contacts.js",
	"Sales Person": ["public/js/partner_contacts.js", "public/js/sales_person.js"],
	"Employee": "public/js/partner_contacts.js",
	"YRP Retailer": "public/js/partner_contacts.js",
	"Item": "public/js/item.js",
	"Purchase Order": "public/js/purchase_order.js",
}
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
after_install = [
	"yrp.yrp.doctype.yrp_notification_template.yrp_notification_template.add_whatsapp_communication_medium",
	"yrp.yrp_partner.setup.setup_partner_users",
	"yrp.yrp_partner.setup.setup_contact_support",
	"yrp.yrp_retail.setup.setup_retail",
	"yrp.yrp_retail.setup.setup_sales_flow",
	"yrp.yrp_retail.item_template.setup_item_sales",
	"yrp.yrp_partner.workspace.setup_partner_workspace",
]

boot_session = ["yrp.yrp_partner.workspace.add_partner_navigation"]

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

permission_query_conditions = {"*": "yrp.yrp_partner.permissions.query_conditions"}
has_permission = {"*": "yrp.yrp_partner.permissions.has_permission"}

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
		"yrp.yrp_stock.doctype.yrp_stock_integrity_check.yrp_stock_integrity_check.run_daily_check",
		"yrp.whatsapp_templates.sync_templates_from_hub",
	],
	"hourly": [
		"yrp.yrp_stock.doctype.yrp_repost_item_valuation.yrp_repost_item_valuation.repost_entries",
		"yrp.yrp_stock.doctype.yrp_stock_valuation_adjustment.yrp_stock_valuation_adjustment.recover_stalled_adjustments",
	],
	"cron": {
		"0 1 * * *": [
			"yrp.tasks.daily",
		],
	},
}

doc_events = {
	"Customer": {"on_update": "yrp.yrp_retail.retailer.sync_customer_partner"},
	"Item": {
		"before_rename": "yrp.yrp_retail.item_template.guard_policy_merge",
		"before_validate": ["yrp.yrp_retail.pricing.lock_pricing_policy", "yrp.yrp_retail.item_template.apply_template"],
		"validate": ["yrp.yrp_retail.category.validate_item_classification", "yrp.yrp_retail.pricing.validate_item_free_flag"],
	},
	"YRP Item Master Template": {"before_rename": "yrp.yrp_retail.item_template.guard_policy_merge", "before_validate": "yrp.yrp_retail.pricing.lock_pricing_policy", "validate": ["yrp.yrp_retail.category.validate_template_classification", "yrp.yrp_retail.pricing.validate_template_free_flag"]},
	"Item Price": {"before_validate": "yrp.yrp_retail.pricing.lock_pricing_policy", "validate": "yrp.yrp_retail.pricing.validate_item_price"},
	"Price List": {"before_validate": "yrp.yrp_retail.pricing.lock_pricing_policy", "validate": "yrp.yrp_retail.pricing.validate_price_list"},
	"Sales Order": {
		"validate": ["yrp.yrp_retail.pricing.validate_sales_document", "yrp.yrp_retail.sales_sources.validate_sales_order"],
		"on_update": "yrp.yrp_retail.sales_sources.refresh_progress",
		"before_update_after_submit": "yrp.yrp_retail.sales_sources.validate_sales_order",
		"on_update_after_submit": "yrp.yrp_retail.sales_sources.refresh_progress",
		"on_submit": "yrp.yrp_retail.sales_sources.refresh_progress",
		"on_cancel": "yrp.yrp_retail.sales_sources.refresh_progress",
		"on_trash": "yrp.yrp_retail.sales_sources.refresh_progress",
	},
	"Packing Slip": {
		"before_validate": "yrp.yrp_retail.packing.prepare_packing_slip",
		"validate": "yrp.yrp_retail.packing.validate_packing_slip",
		"before_update_after_submit": "yrp.yrp_retail.packing.guard_packing_status",
		"before_cancel": "yrp.yrp_retail.packing.prevent_delivered_cancellation",
		"on_trash": "yrp.yrp_retail.packing.prevent_delivered_cancellation",
		"on_submit": "yrp.yrp_retail.packing.refresh_progress",
		"on_cancel": "yrp.yrp_retail.packing.refresh_progress",
		"on_update_after_submit": "yrp.yrp_retail.packing.refresh_progress",
	},
	"YRP Retail Order": {
		"validate": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"before_cancel": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"before_update_after_submit": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"on_trash": "yrp.yrp_retail.sales_sources.protect_retail_source",
	},
	"YRP Retail Order Summary": {
		"validate": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"before_cancel": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"before_update_after_submit": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"on_trash": "yrp.yrp_retail.sales_sources.protect_retail_source",
	},
	"Sales Person": {"onload": "yrp.yrp_partner.contacts.load_contacts", "validate": "yrp.yrp_retail.setup.validate_sales_person"},
	"Employee": {"onload": "yrp.yrp_partner.contacts.load_contacts"},
	"YRP Retailer": {"onload": "yrp.yrp_partner.contacts.load_contacts"},
	"Contact": {
		"before_validate": "yrp.yrp_partner.users.prepare_contact_user",
		"on_trash": "yrp.yrp_partner.sync.sync_contact",
	},
	"YRP Partner Type": {
		"on_update": "yrp.yrp_partner.workspace.clear_partner_navigation_cache",
		"on_trash": "yrp.yrp_partner.workspace.clear_partner_navigation_cache",
	},
	"*": {
		"before_rename": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_validate": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_submit": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_cancel": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_update_after_submit": "yrp.yrp_partner.permissions.prevent_partner_write",
		"on_trash": "yrp.yrp_partner.permissions.prevent_partner_write",
		"on_update": "yrp.yrp_partner.sync.sync_document",
		"on_update_after_submit": "yrp.yrp_partner.sync.sync_document",
	},
	'YRP Stock Settings': {
		"on_update": "yrp.stock.dimensions.clear_dimension_cache",
	},
	"Item Attribute": {
		"before_save": "yrp.yrp.doctype.yrp_item.yrp_item.prevent_yrp_variant_code_change",
	},
	"Stock Entry": {
		"before_submit": "yrp.erpnext_stock_guard.reject_yrp_stock_items",
	},
	"Purchase Receipt": {
		"before_submit": "yrp.erpnext_stock_guard.reject_yrp_stock_items",
	},
	"Subcontracting Receipt": {
		"before_submit": "yrp.erpnext_stock_guard.reject_yrp_stock_items",
	},
	"Delivery Note": {
		"before_validate": "yrp.yrp_retail.packing.lock_packing",
		"validate": ["yrp.yrp_retail.pricing.validate_sales_document", "yrp.yrp_retail.packing.validate_delivery_note"],
		"before_update_after_submit": "yrp.yrp_retail.packing.validate_delivery_note",
		"before_cancel": "yrp.yrp_retail.packing.prevent_delivered_cancellation",
		"on_trash": "yrp.yrp_retail.packing.prevent_delivered_cancellation",
		"before_submit": "yrp.erpnext_stock_guard.reject_yrp_stock_items",
	},
	"Stock Reconciliation": {
		"before_submit": "yrp.erpnext_stock_guard.reject_yrp_stock_items",
	},
	"Stock Reservation Entry": {
		"before_submit": "yrp.erpnext_stock_guard.reject_yrp_stock_items",
	},
	"Purchase Invoice": {
		"before_submit": "yrp.erpnext_stock_guard.reject_yrp_stock_items",
	},
	"Sales Invoice": {
		"validate": "yrp.yrp_retail.pricing.validate_sales_document",
		"before_submit": "yrp.erpnext_stock_guard.reject_yrp_stock_items",
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
	"yrp.yrp_partner.workspace.setup_partner_workspace",
	"yrp.yrp_partner.setup.setup_partner_users",
	"yrp.yrp_partner.setup.setup_contact_support",
	"yrp.yrp_retail.setup.setup_retail",
	"yrp.yrp_retail.setup.setup_sales_flow",
	"yrp.yrp_retail.item_template.setup_item_sales",
	"yrp.stock.dimensions.create_dimension_fields",
	"yrp.patches.add_sle_composite_index.execute",
	"yrp.yrp.doctype.yrp_item.yrp_item.ensure_variant_tuple_unique_index",
	"yrp.yrp.doctype.yrp_notification_template.yrp_notification_template.add_whatsapp_communication_medium",
]

# Testing
# -------

# before_tests = "yrp.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
extend_doctype_class = {
	"Workspace": "yrp.yrp_partner.workspace.PartnerWorkspaceMixin",
	"User": "yrp.yrp_partner.users.PartnerUserMixin",
	"YRP Retail Order": "yrp.yrp_retail.pricing.PricingLockMixin",
	"YRP Retail Order Summary": "yrp.yrp_retail.pricing.PricingLockMixin",
	"Sales Order": ["yrp.yrp_retail.pricing.RetailSalesPricingMixin", "yrp.yrp_retail.pricing.PricingLockMixin"],
	"Delivery Note": ["yrp.yrp_retail.pricing.RetailSalesPricingMixin", "yrp.yrp_retail.packing.PackingTrackingMixin"],
	"Packing Slip": "yrp.yrp_retail.packing.PackingTrackingMixin",
	"Sales Invoice": "yrp.yrp_retail.pricing.RetailSalesPricingMixin",
	"Item": ["yrp.yrp.doctype.yrp_item.yrp_item.YRPItemMixin", "yrp.yrp_retail.item_template.ItemSalesTemplateMixin", "yrp.yrp_retail.pricing.PricingLockMixin"],
	"Item Price": "yrp.yrp_retail.pricing.PricingLockMixin",
	"Price List": "yrp.yrp_retail.pricing.PricingLockMixin",
	"Purchase Order": "yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order.YRPPurchaseOrderMixin",
	"Supplier": "yrp.yrp.doctype.yrp_supplier.yrp_supplier.YRPSupplierMixin",
	"Warehouse": "yrp.yrp.doctype.yrp_warehouse.yrp_warehouse.YRPWarehouseMixin",
}

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {}
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
