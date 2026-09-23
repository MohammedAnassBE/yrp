"""Synthetic navigation tests: no Customer, Contact, User or Partner records."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from yrp.yrp_partner import workspace


class TestPartnerWorkspace(TestCase):
	def test_sources_follow_configuration_and_are_unique(self):
		with (
			patch.object(workspace.frappe, "get_all", return_value=["Sample Party", "Sample Party"]),
			patch.object(workspace.frappe, "has_permission", return_value=True),
		):
			self.assertEqual(
				[link.link_to for link in workspace.get_partner_links()],
				["YRP Partner Type", "YRP Partner", "Sample Party"],
			)

	def test_links_do_not_expose_unreadable_sources_or_settings(self):
		with (
			patch.object(workspace.frappe, "get_all", return_value=["Sample Party", "Private Party"]),
			patch.object(
				workspace.frappe, "has_permission",
				side_effect=lambda doctype, ptype: doctype in ("YRP Partner", "Sample Party"),
			),
		):
			self.assertEqual(
				[link.link_to for link in workspace.get_partner_links()],
				["YRP Partner", "Sample Party"],
			)

	def test_configuration_is_read_again_instead_of_caching_source_links(self):
		with (
			patch.object(workspace.frappe, "get_all", side_effect=[["Sample Party"], []]),
			patch.object(workspace.frappe, "has_permission", return_value=True),
		):
			self.assertIn("Sample Party", [link.link_to for link in workspace.get_partner_links()])
			self.assertNotIn("Sample Party", [link.link_to for link in workspace.get_partner_links()])

	def test_mixin_changes_only_its_own_workspace(self):
		class Base:
			def get_link_groups(self):
				return ["unrelated workspace"]

		class Workspace(workspace.PartnerWorkspaceMixin, Base):
			pass

		doc = Workspace()
		doc.name = "Unrelated Workspace"
		self.assertEqual(doc.get_link_groups(), ["unrelated workspace"])
		doc.name = workspace.WORKSPACE
		with patch.object(workspace, "get_partner_links", return_value=["configured links"]):
			self.assertEqual(doc.get_link_groups()[0].links, ["configured links"])

	def test_sidebar_obeys_native_visibility_and_removes_old_static_sources(self):
		home = {"link_type": "Workspace", "link_to": workspace.WORKSPACE}
		boot = {
			"workspace_sidebar_item": {
				"yrp partner": {"items": [home, {"link_type": "DocType", "link_to": "Old Party"}]},
				"unrelated": {"items": ["untouched"]},
			}
		}
		links = [frappe._dict(link_to=name, link_type="DocType") for name in ("Allowed", "Restricted")]
		views = Mock()
		views.is_item_allowed.side_effect = lambda name, kind: name == "Allowed"
		with (
			patch.object(workspace, "get_partner_links", return_value=links),
			patch.object(workspace, "DeskViews", return_value=views),
			patch.object(workspace.frappe, "get_user", return_value=SimpleNamespace(can_read=["Allowed"])),
		):
			workspace.add_partner_navigation(boot)
		self.assertEqual(boot["workspace_sidebar_item"]["yrp partner"]["items"], [home, links[0]])
		self.assertEqual(boot["workspace_sidebar_item"]["unrelated"]["items"], ["untouched"])

	def test_navigation_does_not_restore_a_hidden_sidebar(self):
		boot = {"workspace_sidebar_item": {}}
		with patch.object(workspace, "get_partner_links") as get_links:
			workspace.add_partner_navigation(boot)
			get_links.assert_not_called()
		self.assertEqual(boot["workspace_sidebar_item"], {})

	def test_navigation_cache_is_invalidated_only_after_commit(self):
		with patch.object(workspace.frappe, "db", create=True, new_callable=Mock) as db:
			workspace.clear_partner_navigation_cache()
			db.after_commit.add.assert_called_once()
			self.assertTrue(callable(db.after_commit.add.call_args.args[0]))

	def test_setup_moves_owned_workspace_and_preserves_native_links(self):
		with (
			patch.object(workspace.frappe, "db", create=True, new_callable=Mock) as db,
			patch.object(workspace.frappe, "rename_doc") as rename,
			patch.object(workspace.frappe, "get_app_path", side_effect=lambda *parts: "/".join(parts)),
			patch("frappe.modules.import_file.import_file_by_path") as import_file,
			patch.object(workspace, "clear_partner_navigation_cache"),
		):
			db.get_value.return_value = frappe._dict(app="yrp", module="YRP Partner")
			db.exists.return_value = False
			workspace.setup_partner_workspace()
			rename.assert_called_once_with(
				"Workspace", "YRP Partner", "YRP Partner Management",
				force=True, merge=False,
				show_alert=False, rebuild_search=False,
			)
			self.assertEqual(import_file.call_count, 2)

	def test_setup_is_repeatable_when_workspace_was_already_renamed(self):
		with (
			patch.object(workspace.frappe, "db", create=True, new_callable=Mock) as db,
			patch.object(workspace.frappe, "rename_doc") as rename,
			patch.object(workspace.frappe, "get_app_path", side_effect=lambda *parts: "/".join(parts)),
			patch("frappe.modules.import_file.import_file_by_path") as import_file,
			patch.object(workspace, "clear_partner_navigation_cache"),
		):
			db.get_value.return_value = None
			workspace.setup_partner_workspace()
			rename.assert_not_called()
			self.assertEqual(import_file.call_count, 2)
