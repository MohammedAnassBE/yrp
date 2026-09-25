app_name = "yrp"
app_title = "YRP"
app_publisher = "Mohammed Anas"
app_description = "Your Resource Planning"
app_email = "mohammedanasman123@gmail.com"
app_license = "mit"

fixtures = [
	{"dt": "Role", "prefix": "00", "filters": [["name", "in", [
		"YRP Customer", "YRP Partner", "YRP Partner Manager", "YRP Retail User",
		"YRP Sales Partner", "YRP Sales Person",
	]]]},
	{"dt": "Custom Field", "filters": [["module", "in", ["YRP", "YRP Partner", "YRP Retail"]]]},
	{"dt": "Custom DocPerm", "filters": [["name", "in", [
		"00c2861ac9", "0331964490", "062796e077", "09d7af98f8",
		"0c7adbd9c1", "161370c40b", "1b79836b41", "1dbaea0360",
		"2619092fd8", "30282036af", "37384db115", "37f7d46e0f",
		"39ad8d660e", "39dc4a44d6", "3a04df3936", "4737676028",
		"47ac8b7005", "4ae40db618", "4d166518ee", "4d16c6e30c",
		"517e0363fa", "550ef1a6fd", "56af4155c3", "5c60eed9f3",
		"60ea79155c", "645ca1776a", "69f0eae1cd", "6e0ac67c1b",
		"714aeb6a41", "74b3a2bc9a", "754ffcf1f9", "77dec3cba5",
		"7b5a2f7664", "7bf8a5e91e", "7d1cfd227d", "7eee773c65",
		"8366u3f46b", "836qk8nqn6", "838be05e8a", "838d6vnvq8",
		"83aaea3goa", "83aiobc96s", "83gbot0k5g", "83h1k9m9fl",
		"83ia3r0s24", "8546d44f09", "8bebfac285", "8hlrtq6mi1",
		"8hs8ao0rhu", "8ht8fek4jd", "8htbjl87ep", "8htcv7503b",
		"8htq4092ic", "8i1t1887gu", "8i22d4jrsu", "8i31jmhlel",
		"8i37kqhs6c", "8i3trs2l4k", "8ierl94634", "8ig26cm5j2",
		"8ig3v12ts8", "8igimqk97o", "8igl3tng46", "8ir5pfe83b",
		"8itbg95vvu", "8iuenef2vj", "8j14dikvnd", "8j19dtsn85",
		"8j1lkf9773", "8j1sc9bg2l", "8j2mhbfms8", "8j2rg56sqb",
		"8j4rbvtkfv", "8ja9t88bf4", "8jaf5r552r", "8jahs6pv2d",
		"8jathcs5up", "8jbam2sraf", "8jbib74pbv", "8jc0amaqam",
		"8jea2tmmc2", "8jecpfa2q0", "8jell1biil", "8jfil7rnv4",
		"8jh8ao1hah", "8jj5c04v4q", "8jkkrg7619", "8jm29kou5k",
		"8jp56d4sh4", "8jp71f53iv", "8jpb62dfp9", "8jpml658b1",
		"8jpnt5q490", "8jpqoefvse", "8jq0qqlrco", "8jrgaf5pjr",
		"8ju4q07ckd", "8ju83fkegs", "8jubq0ke39", "8juf1pikbg",
		"8julpq3tkm", "8k08e7jsa7", "8k1297iapb", "8k17hcaktb",
		"8k1f02o4ab", "8k1pb9jjqv", "8k1qoi86tp", "8k219frb07",
		"8k25s1jvs8", "8k2a4uidfu", "8k2rl880ct", "8k3v15sh07",
		"8k57c7mov2", "8k5h31flco", "8k5pqeifvm", "8k5rrm0ghb",
		"8k6c4itehd", "8kckp8vajm", "8kcuraeslt", "8kdjs4a98b",
		"8kdo2kv6bo", "8kdqoufq9k", "92713b89e1", "94e9f39822",
		"9fbbc97ddb", "9fc5d45456", "YRP-Customer-Account-YRP Customer-0-0", "YRP-Customer-Account-YRP Sales Person-0-0",
		"YRP-Customer-Address-YRP Customer-0-0", "YRP-Customer-Address-YRP Sales Partner-0-0", "YRP-Customer-Address-YRP Sales Person-0-0", "YRP-Customer-Blanket Order-System Manager-0-0",
		"YRP-Customer-Blanket Order-YRP Customer-0-0", "YRP-Customer-Blanket Order-YRP Sales Partner-0-0", "YRP-Customer-Blanket Order-YRP Sales Person-0-0", "YRP-Customer-Company-YRP Customer-0-0",
		"YRP-Customer-Company-YRP Sales Person-0-0", "YRP-Customer-Contact-YRP Customer-0-0", "YRP-Customer-Contact-YRP Sales Partner-0-0", "YRP-Customer-Contact-YRP Sales Person-0-0",
		"YRP-Customer-Customer-YRP Customer-0-0", "YRP-Customer-Delivery Note-YRP Customer-0-0", "YRP-Customer-Delivery Note-YRP Sales Partner-0-0", "YRP-Customer-Delivery Note-YRP Sales Person-0-0",
		"YRP-Customer-Dunning-Accounts Manager-0-0", "YRP-Customer-Dunning-Accounts User-0-0", "YRP-Customer-Dunning-System Manager-0-0", "YRP-Customer-Dunning-YRP Customer-0-0",
		"YRP-Customer-Dunning-YRP Sales Partner-0-0", "YRP-Customer-Dunning-YRP Sales Person-0-0", "YRP-Customer-GL Entry-YRP Customer-0-0", "YRP-Customer-GL Entry-YRP Sales Partner-0-0",
		"YRP-Customer-GL Entry-YRP Sales Person-0-0", "YRP-Customer-Installation Note-Sales User-0-0", "YRP-Customer-Installation Note-Sales User-1-0", "YRP-Customer-Installation Note-YRP Customer-0-0",
		"YRP-Customer-Installation Note-YRP Sales Partner-0-0", "YRP-Customer-Installation Note-YRP Sales Person-0-0", "YRP-Customer-Loyalty Point Entry-Accounts Manager-0-0", "YRP-Customer-Loyalty Point Entry-Accounts User-0-0",
		"YRP-Customer-Loyalty Point Entry-Auditor-0-0", "YRP-Customer-Loyalty Point Entry-YRP Customer-0-0", "YRP-Customer-Loyalty Point Entry-YRP Sales Partner-0-0", "YRP-Customer-Loyalty Point Entry-YRP Sales Person-0-0",
		"YRP-Customer-Maintenance Schedule-Maintenance Manager-0-0", "YRP-Customer-Maintenance Schedule-YRP Customer-0-0", "YRP-Customer-Maintenance Schedule-YRP Sales Partner-0-0", "YRP-Customer-Maintenance Schedule-YRP Sales Person-0-0",
		"YRP-Customer-Maintenance Visit-Maintenance User-0-0", "YRP-Customer-Maintenance Visit-YRP Customer-0-0", "YRP-Customer-Maintenance Visit-YRP Sales Partner-0-0", "YRP-Customer-Maintenance Visit-YRP Sales Person-0-0",
		"YRP-Customer-Packing Slip-YRP Customer-0-0", "YRP-Customer-Packing Slip-YRP Sales Person-0-0", "YRP-Customer-Quotation-Maintenance Manager-0-0", "YRP-Customer-Quotation-Maintenance Manager-1-0",
		"YRP-Customer-Quotation-Maintenance User-0-0", "YRP-Customer-Quotation-Maintenance User-1-0", "YRP-Customer-Quotation-Sales Manager-0-0", "YRP-Customer-Quotation-Sales Manager-1-0",
		"YRP-Customer-Quotation-Sales User-0-0", "YRP-Customer-Quotation-Sales User-1-0", "YRP-Customer-Quotation-YRP Customer-0-0", "YRP-Customer-Quotation-YRP Sales Partner-0-0",
		"YRP-Customer-Quotation-YRP Sales Person-0-0", "YRP-Customer-Sales Invoice-YRP Customer-0-0", "YRP-Customer-Sales Invoice-YRP Sales Partner-0-0", "YRP-Customer-Sales Invoice-YRP Sales Person-0-0",
		"YRP-Customer-Sales Order-YRP Customer-0-0", "YRP-Customer-Sales Order-YRP Sales Person-0-0", "YRP-Customer-Shipment-Stock Manager-0-0", "YRP-Customer-Shipment-System Manager-0-0",
		"YRP-Customer-Shipment-YRP Customer-0-0", "YRP-Customer-Shipment-YRP Sales Partner-0-0", "YRP-Customer-Shipment-YRP Sales Person-0-0", "YRP-Customer-Warranty Claim-Maintenance User-0-0",
		"YRP-Customer-Warranty Claim-YRP Customer-0-0", "YRP-Customer-Warranty Claim-YRP Sales Partner-0-0", "YRP-Customer-Warranty Claim-YRP Sales Person-0-0", "YRP-Customer-YRP Customer Stock-System Manager-0-0",
		"YRP-Customer-YRP Customer Stock-YRP Customer-0-0", "YRP-Customer-YRP Customer Stock-YRP Partner Manager-0-0", "YRP-Customer-YRP Customer Stock-YRP Partner-0-0", "YRP-Customer-YRP Customer Stock-YRP Retail User-0-0",
		"YRP-Customer-YRP Customer Stock-YRP Sales Partner-0-0", "YRP-Customer-YRP Customer Stock-YRP Sales Person-0-0", "YRP-Customer-YRP Retail Order Summary-YRP Customer-0-0", "YRP-Customer-YRP Retail Order Summary-YRP Sales Partner-0-0",
		"YRP-Customer-YRP Retail Order-YRP Customer-0-0", "YRP-Customer-YRP Retail Order-YRP Sales Partner-0-0", "YRP-Customer-YRP Retailer-YRP Customer-0-0", "YRP-Customer-YRP Retailer-YRP Sales Partner-0-0",
		"YRP-Customer-YRP Visit-YRP Customer-0-0", "YRP-Customer-YRP Visit-YRP Sales Partner-0-0", "a01e676196", "a16c09ef88",
		"a43177a5ec", "a4d41567a5", "a94e86b55e", "amfplc4g5q",
		"aqsou10dkf", "ar0br1itbu", "ar1j6bl7br", "b3315b504e",
		"b54e4e095d", "b5827d489d", "ba8337a2c6", "bc1c352944",
		"bcbdfe9ecc", "c26a1f8143", "c6339bcad7", "c678f4e6c9",
		"c712eaa80c", "c7616de5be", "c91453fc8c", "ccffb994a7",
		"cf548a9eb4", "cf8ncb7mmm", "cfat20h5bi", "cmihm5rrq3",
		"cmjd9svjdn", "cmkcakon14", "cmkcdd83is", "cmkmp4bht9",
		"cmkprjn695", "cmkpuqs2op", "cmlkur10f0", "cmlnkfp60j",
		"d117bcfe80", "d2bab5ba92", "d6193c7e5d", "da6929fd0f",
		"dcd7fb8651", "ddef9340c1", "e45f58e09e", "ea37722a36",
		"eed2ed30de", "f05f2c0d61", "f6a4db4693", "f77525760e",
		"fc10fd65f2", "ff126c57d8", "o2hngl8v6e", "o2i54c38gq",
		"o2id2t2a9r", "o2imvkii2g", "o2qih5s1i2", "o2v48sg3oo",
		"o34glqijii", "o3676fv5dr", "o3896apq57", "o3a629m9mh",
		"o3a8gjme01", "o3aejd0k56", "o3ag2kcr06", "o3bfjsds8t",
		"o3d5i91a0e", "o3fi9gds0s", "o3g4573s8p", "o3glce3btc",
		"rbp65ebmc2", "rbueivcpe1", "sttehf8n1e", "su0n9tilcf",
	]]]},
	{"dt": "Custom Role", "filters": [["name", "in", [
		"7efd7215a2", "YRP-Customer-Report-YRP Packing and Delivery", "YRP-Customer-Report-YRP Retail Demand", "YRP-Customer-Report-YRP Retail Summary Allocation",
		"YRP-Customer-Report-YRP Sales Order Fulfilment", "a07c31b3ba", "e3d4c742f2",
	]]]},
	{"dt": "Workflow", "filters": [["name", "in", ["Item Price Workflow", "Process Cost Workflow"]]]},
	{"dt": "Workflow State", "filters": [["name", "in", ["Draft", "Approval Pending", "Approved", "Rejected", "Expired"]]]},
	{"dt": "Workflow Action Master", "filters": [["name", "in", ["Submit", "Approve", "Reject", "Expired"]]]},
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
# Static configuration is synchronized from the fixtures declared above.

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
		"before_validate": "yrp.yrp_partner.sales_roles.prepare_sales_order",
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
		"before_discard": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"before_update_after_submit": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"on_trash": "yrp.yrp_retail.sales_sources.protect_retail_source",
	},
	"YRP Retail Order Summary": {
		"validate": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"before_cancel": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"before_discard": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"before_update_after_submit": "yrp.yrp_retail.sales_sources.protect_retail_source",
		"on_trash": "yrp.yrp_retail.sales_sources.protect_retail_source",
	},
	"Sales Person": {"onload": "yrp.yrp_partner.contacts.load_contacts", "validate": "yrp.yrp_retail.setup.validate_sales_person"},
	"Employee": {"onload": "yrp.yrp_partner.contacts.load_contacts"},
	"YRP Retailer": {"onload": "yrp.yrp_partner.contacts.load_contacts"},
	"Contact": {
		"before_validate": "yrp.yrp_partner.sync.validate_contact_links",
		"on_trash": "yrp.yrp_partner.sync.sync_contact",
	},
	"YRP Partner Type": {
		"on_update": "yrp.yrp_partner.workspace.clear_partner_navigation_cache",
		"on_trash": "yrp.yrp_partner.workspace.clear_partner_navigation_cache",
	},
	"*": {
		"before_rename": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_validate": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_save": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_submit": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_cancel": "yrp.yrp_partner.permissions.prevent_partner_write",
		"before_discard": "yrp.yrp_partner.permissions.prevent_partner_write",
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
		"on_update": "yrp.yrp_partner.sync.sync_user_memberships",
		# Per-user UI storage lifecycle (PER_USER_UI_SPEC.md §3.3): the YRP UI
		# Preference docname == user; keep it in sync on offboarding/rename.
		"on_trash": ["yrp.yrp_partner.sync.sync_user_memberships", "yrp.yrp.api.ui_config.delete_ui_preference_for_user"],
		"before_rename": "yrp.yrp.api.ui_config.merge_ui_preference_for_user",
		"after_rename": ["yrp.yrp_partner.sync.sync_user_memberships", "yrp.yrp.api.ui_config.rename_ui_preference_for_user"],
	},
}

after_migrate = [
	"yrp.stock.dimensions.create_dimension_fields",
	"yrp.patches.add_sle_composite_index.execute",
	"yrp.yrp.doctype.yrp_item.yrp_item.ensure_variant_tuple_unique_index",
]

# Testing
# -------

# before_tests = "yrp.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
extend_doctype_class = {
	"Report": "yrp.yrp_partner.customer_reports.CustomerReportMixin",
	"Prepared Report": "yrp.yrp_partner.customer_reports.CustomerPreparedReportMixin",
	"Workspace": "yrp.yrp_partner.workspace.PartnerWorkspaceMixin",
	"YRP Retail Order": "yrp.yrp_retail.pricing.PricingLockMixin",
	"YRP Retail Order Summary": "yrp.yrp_retail.pricing.PricingLockMixin",
	"Sales Order": ["yrp.yrp_retail.pricing.RetailSalesPricingMixin", "yrp.yrp_retail.pricing.PricingLockMixin", "yrp.yrp_partner.sales_order.PartnerSalesOrderMixin"],
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
auth_hooks = ["yrp.yrp_partner.customer_api.guard_request"]

override_whitelisted_methods = {
	"erpnext.accounts.doctype.account.account.get_root_company": "yrp.yrp_partner.customer_api.account_root_company",
	"erpnext.setup.doctype.company.company.get_children": "yrp.yrp_partner.customer_api.company_children",
	"erpnext.selling.doctype.sales_order.sales_order.get_events": "yrp.yrp_partner.customer_api.sales_order_events",
	"frappe.desk.query_report.run": "yrp.yrp_partner.customer_reports.run",
	"frappe.desk.query_report.export_query": "yrp.yrp_partner.customer_reports.export_query",
}
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
