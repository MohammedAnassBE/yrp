import gzip
import json
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.test_api import make_request
from frappe.utils import add_to_date, get_test_client, now_datetime

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.test_uom import _india_compliance_item_fields, _item_group
from yrp.web_reports.cleanup import clear_old_jobs
from yrp.web_reports.jobs import (
	_enqueue_lazy_cleanup,
	_store_completed_result,
	download_report_job,
	execute_report_job,
	get_report_job,
	read_gzip_attachment,
	reconcile_report_job,
	run_web_report,
)
from yrp.web_reports.registry import WebReport, clear_web_report_cache, get_web_report
from yrp.web_reports.test_support import report_access_entries
from yrp.yrp.doctype.yrp_report_job.yrp_report_job import YRPReportJob


class TestReportJobAccess(IntegrationTestCase):
	def test_run_checks_access_before_invalid_filters(self):
		with report_access_entries() as fixture:
			frappe.set_user(fixture.denied_user)
			for kind, entry in fixture.entries.items():
				with self.subTest(kind=kind):
					with self.assertRaises(frappe.PermissionError):
						run_web_report(entry.key, {"based_on": "WO Status"})

	def test_poll_and_download_check_access_before_job_lookup_and_filters(self):
		with report_access_entries() as fixture:
			for kind, entry in fixture.entries.items():
				frappe.set_user("Administrator")
				job = frappe.get_doc(
					{
						"doctype": "YRP Report Job",
						"name": frappe.generate_hash(length=20),
						"report_key": entry.key,
						"kind": kind,
						"owner": fixture.denied_user,
						"filters": frappe.as_json({"based_on": "WO Status"}),
						"status": "Queued",
					}
				)
				job.db_insert()
				try:
					frappe.set_user(fixture.denied_user)
					for endpoint in (get_report_job, download_report_job):
						for name in (job.name, f"missing-{job.name}"):
							with self.subTest(kind=kind, endpoint=endpoint.__name__, job=name):
								with self.assertRaises(frappe.PermissionError):
									endpoint(entry.key, name)
				finally:
					frappe.set_user("Administrator")
					frappe.delete_doc("YRP Report Job", job.name, ignore_permissions=True, force=True)


class TestReportJobs(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.users = [_make_user("report-owner"), _make_user("report-other")]
		cls.ai_bot_user = _make_user("report-ai-bot", roles=("AI Bot",))
		cls.plain_user = _make_user("report-plain", roles=())
		cls.item = _make_physical_item()
		company = frappe.db.get_value("Company", {}, "name")
		cls.warehouse = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": f"Report Job {frappe.generate_hash(length=8)}",
				"company": company,
			}
		).insert(ignore_permissions=True).name
		cls.ledger_entry = _insert_ledger(cls.item, cls.warehouse)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		_delete_jobs()
		for doctype, name in (
			("YRP Stock Ledger Entry", cls.ledger_entry),
			("Warehouse", cls.warehouse),
			("Item", cls.item),
		):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
		frappe.db.commit()
		for user in [*cls.users, cls.ai_bot_user, cls.plain_user]:
			_delete_fixture_user(user)
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		_delete_jobs()
		self.fixture_records = []
		self.assigned_roles = []
		clear_web_report_cache()
		entry = get_web_report("stock-balance")
		frappe.flags.yrp_web_report_overrides = {
			"stock-balance-job-test": replace(
				entry,
				key="stock-balance-job-test",
				background="always",
			)
		}
		clear_web_report_cache()

	def tearDown(self):
		frappe.set_user("Administrator")
		_delete_jobs()
		self._delete_fixture_records()
		frappe.flags.yrp_web_report_overrides = None
		clear_web_report_cache()
		frappe.db.commit()

	def test_no_long_worker_rejects_request_without_creating_a_job(self):
		with (
			patch("yrp.web_reports.jobs.frappe.enqueue"),
			patch("yrp.web_reports.jobs.get_workers", return_value=[]),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "background worker unavailable"):
				run_web_report("stock-balance-job-test", _stock_filters())
		self.assertFalse(frappe.db.exists("YRP Report Job", {"report_key": "stock-balance-job-test"}))

	def test_deleted_job_reports_not_found_to_its_owner(self):
		frappe.set_user(self.users[0])
		with self.assertRaisesRegex(frappe.DoesNotExistError, "no longer available"):
			get_report_job("stock-balance-job-test", "missing-job", include_result=1)
		with self.assertRaisesRegex(frappe.DoesNotExistError, "no longer available"):
			download_report_job("stock-balance-job-test", "missing-job")

	def test_worker_stores_owner_isolated_result_as_administrator(self):
		response = self._enqueue(self.users[0])
		job_name = response["job"]["name"]
		frappe.set_user(self.users[0])
		execute_report_job(job_name)

		job = frappe.get_doc("YRP Report Job", job_name)
		self.assertEqual(job.status, "Completed", job.error_message)
		result = get_report_job("stock-balance-job-test", job_name, include_result=1)
		self.assertTrue(result["result"])
		self.assertIn(self.item, {row["item"] for row in result["result"]})

		file_doc = _job_file(job_name)
		self.assertEqual(file_doc.owner, "Administrator")
		self.assertFalse(frappe.has_permission("File", "read", doc=file_doc, user=self.users[0]))
		self.assertFalse(frappe.has_permission("File", "read", doc=file_doc, user=self.users[1]))
		self.assertFalse(frappe.has_permission("YRP Report Job", "read", doc=job, user=self.users[1]))
		with self.assertRaises(frappe.PermissionError):
			frappe.has_permission("YRP Report Job", "read", doc=job, user=self.users[1], throw=True)

		frappe.set_user(self.users[1])
		with self.assertRaises(frappe.PermissionError):
			get_report_job("stock-balance-job-test", job_name, include_result=1)
		with self.assertRaises(frappe.PermissionError):
			download_report_job("stock-balance-job-test", job_name)
		with self.assertRaises(frappe.PermissionError):
			frappe.share.add("YRP Report Job", job_name, user=self.users[1])

	def test_direct_job_access_is_denied_to_ai_bot_and_plain_users(self):
		job_name = self._complete_job()
		job = frappe.get_doc("YRP Report Job", job_name)
		file_doc = _job_file(job_name)
		frappe.db.commit()

		for user in (self.ai_bot_user, self.plain_user):
			frappe.set_user(user)
			try:
				listed_jobs = frappe.get_list(
					"YRP Report Job",
					filters={"name": job_name},
					pluck="name",
				)
			except frappe.PermissionError:
				listed_jobs = []
			self.assertEqual(listed_jobs, [])
			self.assertFalse(frappe.has_permission("YRP Report Job", "read", doc=job, user=user))
			self.assertFalse(file_doc.has_permission("read", user=user))
			self.assertEqual(_rest_get_status(user, "YRP Report Job", job_name), 403)

		frappe.set_user(self.users[0])
		result = get_report_job("stock-balance-job-test", job_name, include_result=1)
		self.assertEqual(result["job"]["status"], "Completed")
		download_report_job("stock-balance-job-test", job_name)
		self.assertTrue(frappe.local.response.filecontent)

	def test_s3_style_gzip_attachment_is_read_through_file_controller(self):
		payload = {"result": [{"value": 4}]}
		attachment = frappe._dict(
			name="FILE-S3",
			file_name="stored-result.json.gz",
			file_url="/api/method/frappe_s3_integration.s3_core.serve_file/result?file_id=1",
		)
		file_doc = frappe._dict(get_content=lambda encodings=(): gzip.compress(json.dumps(payload).encode()))
		with (
			patch("yrp.web_reports.jobs.get_attachments", return_value=[attachment]),
			patch("yrp.web_reports.jobs.frappe.get_doc", return_value=file_doc),
		):
			self.assertEqual(read_gzip_attachment(frappe._dict(name="JOB-S3")), payload)

	def test_s3_missing_object_is_normalized_as_a_missing_attachment(self):
		attachment = frappe._dict(
			name="FILE-S3-MISSING",
			file_name="stored-result.json.gz",
			file_url="/api/method/frappe_s3_integration.s3_core.serve_file/result?file_id=1",
		)
		exception = _StorageNotFoundError("NoSuchKey")
		file_doc = frappe._dict(get_content=lambda encodings=(): (_ for _ in ()).throw(exception))
		with (
			patch("yrp.web_reports.jobs.get_attachments", return_value=[attachment]),
			patch("yrp.web_reports.jobs.frappe.get_doc", return_value=file_doc),
			self.assertRaisesRegex(frappe.ValidationError, "bytes are missing"),
		):
			read_gzip_attachment(frappe._dict(name="JOB-S3-MISSING"))

	def test_completed_result_is_reused_until_refresh(self):
		first = self._complete_job()

		reused = self._enqueue()
		self.assertEqual(reused["job"]["name"], first)
		self.assertTrue(reused["reused"])

		refreshed = self._enqueue(refresh=1)
		self.assertNotEqual(refreshed["job"]["name"], first)
		self.assertFalse(refreshed["reused"])

	def test_completed_result_older_than_24_hours_is_not_reused(self):
		first = self._complete_job()
		frappe.db.set_value(
			"YRP Report Job",
			first,
			"finished_at",
			add_to_date(now_datetime(), hours=-25),
			update_modified=False,
		)

		response = self._enqueue()

		self.assertNotEqual(response["job"]["name"], first)
		self.assertFalse(response["reused"])

	def test_identical_in_flight_request_returns_same_job(self):
		first = self._enqueue()
		second = self._enqueue()

		self.assertEqual(second["job"]["name"], first["job"]["name"])
		self.assertEqual(
			frappe.db.count("YRP Report Job", {"report_key": "stock-balance-job-test"}),
			1,
		)

	def test_concurrent_transactions_return_one_committed_job(self):
		site = frappe.local.site
		sites_path = frappe.local.sites_path
		barrier = threading.Barrier(2)
		results = []
		result_lock = threading.Lock()

		def request_job():
			frappe.init(site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			frappe.set_user(self.users[0])
			try:
				barrier.wait(timeout=10)
				response = run_web_report("stock-balance--test-always", dict(self.stock_filters))
				frappe.db.commit()
				outcome = (response["job"]["name"], None)
			except Exception as exception:
				frappe.db.rollback()
				outcome = (None, f"{exception}\n{traceback.format_exc()}")
			finally:
				frappe.destroy()
			with result_lock:
				results.append(outcome)

		threads = [threading.Thread(target=request_job, daemon=True) for _index in range(2)]
		with (
			patch("yrp.web_reports.jobs.frappe.enqueue"),
			patch("yrp.web_reports.jobs.get_workers", return_value=[object()]),
		):
			for thread in threads:
				thread.start()
			for thread in threads:
				thread.join(timeout=20)

		self.assertTrue(all(not thread.is_alive() for thread in threads), results)
		self.assertFalse([error for _name, error in results if error], results)
		self.assertEqual(len({name for name, _error in results}), 1)
		frappe.db.rollback()
		self.assertEqual(
			frappe.db.count("YRP Report Job", {"report_key": "stock-balance--test-always"}),
			1,
		)

	def test_duplicate_insert_finds_winner_after_active_key_is_cleared(self):
		job_name = self._complete_job()
		winner = frappe.get_doc("YRP Report Job", job_name)
		frappe.set_user(self.users[0])
		with (
			patch("frappe.model.document.Document.insert", side_effect=frappe.DuplicateEntryError),
			patch("yrp.web_reports.jobs.frappe.enqueue"),
			patch("yrp.web_reports.jobs.get_workers", return_value=[object()]),
		):
			response = run_web_report("stock-balance-job-test", self.stock_filters, refresh=1)

		self.assertEqual(response["job"]["name"], winner.name)
		self.assertEqual(response["job"]["status"], "Completed")
		self.assertTrue(response["result"])
		self.assertIn(self.item, {row["item"] for row in response["result"]})

	def test_reconciliation_observes_rq_state_and_enqueue_grace(self):
		queued = frappe.get_doc("YRP Report Job", self._enqueue()["job"]["name"])
		with patch("yrp.web_reports.jobs.get_job_status", return_value=None):
			self.assertEqual(reconcile_report_job(queued).status, "Queued")

		frappe.db.set_value(
			"YRP Report Job",
			queued.name,
			"creation",
			add_to_date(now_datetime(), minutes=-3),
			update_modified=False,
		)
		with patch("yrp.web_reports.jobs.get_job_status", return_value=None):
			lost = reconcile_report_job(queued.name)
		self.assertEqual((lost.status, lost.error_kind, lost.active_key), ("Error", "lost", None))

		started = frappe.get_doc("YRP Report Job", self._enqueue(refresh=1)["job"]["name"])
		started.db_set("status", "Started", commit=True)
		with patch("yrp.web_reports.jobs.get_job_status", return_value="failed"):
			lost = reconcile_report_job(started.name)
		self.assertEqual((lost.status, lost.error_kind), ("Error", "lost"))

	def test_queued_pre_entry_failure_allows_a_new_job(self):
		first = self._enqueue()["job"]["name"]
		with patch("yrp.web_reports.jobs.get_job_status", return_value="failed"):
			lost = reconcile_report_job(first)
		self.assertEqual((lost.status, lost.error_kind, lost.active_key), ("Error", "lost", None))

		second = self._enqueue()

		self.assertNotEqual(second["job"]["name"], first)

	def test_old_healthy_queued_job_is_not_reconciled_as_lost(self):
		job = frappe.get_doc("YRP Report Job", self._enqueue()["job"]["name"])
		frappe.db.set_value(
			"YRP Report Job",
			job.name,
			"creation",
			add_to_date(now_datetime(), minutes=-31),
			update_modified=False,
		)
		with patch("yrp.web_reports.jobs.get_job_status", return_value="queued"):
			self.assertEqual(reconcile_report_job(job.name).status, "Queued")

	def test_late_completion_recovers_lost_job_but_discards_expired_job(self):
		lost_name = self._enqueue()["job"]["name"]
		lost = frappe.get_doc("YRP Report Job", lost_name)
		lost.db_set("status", "Started", commit=True)
		with patch("yrp.web_reports.jobs.get_job_status", return_value="failed"):
			reconcile_report_job(lost_name)
		_store_completed_result(frappe.get_doc("YRP Report Job", lost_name), {"result": [{"value": 1}]})
		self.assertEqual(frappe.db.get_value("YRP Report Job", lost_name, "status"), "Completed")

		expired_name = self._enqueue(refresh=1)["job"]["name"]
		expired = frappe.get_doc("YRP Report Job", expired_name)
		expired.db_set("status", "Started", commit=True)
		expired.db_set({"status": "Expired", "active_key": None}, commit=True)
		_store_completed_result(expired, {"result": [{"value": 2}]})
		self.assertEqual(frappe.db.get_value("YRP Report Job", expired_name, "status"), "Expired")
		self.assertFalse(
			frappe.db.exists(
				"File",
				{"attached_to_doctype": "YRP Report Job", "attached_to_name": expired_name},
			)
		)

	def test_failure_after_file_insert_rolls_back_attachment(self):
		response = self._enqueue()
		job_name = response["job"]["name"]
		frappe.set_user(self.users[0])
		with patch("yrp.web_reports.jobs._transition_completed", side_effect=RuntimeError("after file insert")):
			execute_report_job(job_name)

		job = frappe.get_doc("YRP Report Job", job_name)
		self.assertEqual((job.status, job.error_kind), ("Error", "failed"))
		self.assertIn("after file insert", job.error_message)
		self.assertIsNone(get_report_job("stock-balance-job-test", job_name)["message"])
		self.assertFalse(
			frappe.db.exists("File", {"attached_to_doctype": "YRP Report Job", "attached_to_name": job_name})
		)

	def test_permission_stamp_expires_completed_results(self):
		job_name = self._complete_job()
		permission = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": self.users[0],
				"allow": "Item",
				"for_value": self.item,
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.users[0])
		response = get_report_job("stock-balance-job-test", job_name)
		self.assertTrue(response["expired"])
		permission.delete(ignore_permissions=True)

		second = self._complete_job()
		frappe.set_user("Administrator")
		frappe.get_doc("User", self.users[0]).add_roles("_Test Role 4")
		frappe.db.commit()
		frappe.set_user(self.users[0])
		self.assertTrue(get_report_job("stock-balance-job-test", second)["expired"])

	def test_mid_run_permission_change_from_another_connection_expires_job(self):
		response = self._enqueue()
		job_name = response["job"]["name"]
		created_permissions = []

		def build_after_revocation(_job, _entry, _filters):
			thread = threading.Thread(
				target=_commit_user_permission,
				args=(
					frappe.local.site,
					frappe.local.sites_path,
					self.users[0],
					self.item,
					created_permissions,
				),
			)
			thread.start()
			thread.join(timeout=15)
			self.assertFalse(thread.is_alive())
			self.assertTrue(created_permissions)
			return {"columns": [], "result": []}

		frappe.set_user(self.users[0])
		with patch("yrp.web_reports.jobs._build_payload", side_effect=build_after_revocation):
			execute_report_job(job_name)
		self.fixture_records.extend(("User Permission", name) for name in created_permissions)

		job = frappe.get_doc("YRP Report Job", job_name)
		self.assertEqual((job.status, job.error_kind), ("Expired", "expired"))
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": job_name}))

	def test_everyone_share_change_expires_completed_result(self):
		job_name = self._complete_job()
		frappe.set_user("Administrator")
		share = frappe.get_doc(
			{
				"doctype": "DocShare",
				"share_doctype": "Item",
				"share_name": self.item,
				"user": "",
				"everyone": 1,
				"read": 1,
			}
		)
		share.db_insert()
		self.fixture_records.append(("DocShare", share.name))

		frappe.set_user(self.users[0])
		self.assertTrue(get_report_job("stock-balance-job-test", job_name)["expired"])

	def test_custom_docperm_delete_and_change_expire_results(self):
		role = self._create_role()
		first_permission = self._create_custom_docperm(role, permlevel=8)
		second_permission = self._create_custom_docperm(role, permlevel=9)
		deleted_job = self._complete_job()
		frappe.set_user("Administrator")
		frappe.delete_doc("Custom DocPerm", first_permission, ignore_permissions=True, force=True)

		frappe.set_user(self.users[0])
		self.assertTrue(get_report_job("stock-balance-job-test", deleted_job)["expired"])

		changed_job = self._complete_job()
		frappe.set_user("Administrator")
		frappe.db.set_value("Custom DocPerm", second_permission, "read", 0)
		frappe.set_user(self.users[0])
		self.assertTrue(get_report_job("stock-balance-job-test", changed_job)["expired"])

	def test_report_role_and_disabled_state_are_rechecked(self):
		report_name, allowed_role = self._create_report_fixture()
		self._register_frappe_report_entry("security-report-test", report_name)
		role_job = self._complete_registered_report("security-report-test")
		frappe.set_user("Administrator")
		frappe.db.delete("Has Role", {"parent": report_name, "role": allowed_role})
		frappe.db.commit()

		frappe.set_user(self.users[0])
		with self.assertRaises(frappe.PermissionError):
			get_report_job("security-report-test", role_job)
		with self.assertRaises(frappe.PermissionError):
			download_report_job("security-report-test", role_job)

		frappe.set_user("Administrator")
		frappe.get_doc("User", self.users[0]).add_roles(allowed_role)
		frappe.get_doc(
			{
				"doctype": "Has Role",
				"parent": report_name,
				"parenttype": "Report",
				"parentfield": "roles",
				"role": allowed_role,
			}
		).db_insert()
		disabled_job = self._complete_registered_report("security-report-test")
		frappe.set_user("Administrator")
		frappe.db.set_value("Report", report_name, "disabled", 1)
		frappe.db.commit()

		frappe.set_user(self.users[0])
		with self.assertRaisesRegex(frappe.ValidationError, "is disabled"):
			get_report_job("security-report-test", disabled_job)

	def test_unstamped_link_dependency_fails_without_attachment(self):
		entry = WebReport(
			key="incomplete-dependency-test",
			kind="callable",
			filters={},
			runner="yrp.web_reports.test_support.report_with_unstamped_link",
			access="yrp.web_reports.test_support.allow_report_access",
			background="always",
		)
		frappe.flags.yrp_web_report_overrides[entry.key] = entry
		clear_web_report_cache()
		frappe.set_user(self.users[0])
		with (
			patch("yrp.web_reports.jobs.frappe.enqueue"),
			patch("yrp.web_reports.jobs.get_workers", return_value=[object()]),
		):
			job_name = run_web_report(entry.key, {})["job"]["name"]
		execute_report_job(job_name)

		job = frappe.get_doc("YRP Report Job", job_name)
		self.assertEqual((job.status, job.error_kind), ("Error", "invalid"))
		self.assertIn("report dependency set incomplete: User", job.error_message)
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": job_name}))

	def test_hard_age_limit_blocks_data_and_download(self):
		job_name = self._complete_job()
		frappe.db.set_value(
			"YRP Report Job",
			job_name,
			"finished_at",
			add_to_date(now_datetime(), hours=-25),
			update_modified=False,
		)

		response = get_report_job("stock-balance-job-test", job_name, include_result=1)

		self.assertTrue(response["expired"])
		self.assertFalse(response["result"])
		with self.assertRaisesRegex(frappe.ValidationError, "not available for download"):
			download_report_job("stock-balance-job-test", job_name)

	def test_missing_reusable_attachment_is_expired_and_rebuilt(self):
		first = self._complete_job()
		file_name = _job_file(first).name
		frappe.set_user("Administrator")
		frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
		frappe.db.commit()

		second = self._enqueue()

		self.assertNotEqual(second["job"]["name"], first)
		self.assertEqual(frappe.db.get_value("YRP Report Job", first, "status"), "Expired")

	def test_missing_local_bytes_with_file_record_are_expired_and_rebuilt(self):
		first = self._complete_job()
		file_doc = _job_file(first)
		Path(file_doc.get_full_path()).unlink()
		self.assertTrue(frappe.db.exists("File", file_doc.name))

		frappe.set_user(self.users[0])
		polled = get_report_job("stock-balance-job-test", first, include_result=1)
		self.assertTrue(polled["expired"])
		second = self._enqueue()

		self.assertNotEqual(second["job"]["name"], first)
		self.assertEqual(frappe.db.get_value("YRP Report Job", first, "status"), "Expired")

	def test_prepared_report_automation_is_disabled_without_db_mutation(self):
		report_name, _allowed_role = self._create_report_fixture()
		self._register_frappe_report_entry("prepared-report-test", report_name)
		observed = []

		def slow_result(report, filters, user):
			observed.append((report.disable_prepared_report_automation, user))
			time.sleep(15.1)
			return {"columns": [], "result": [{"name": self.item}]}

		job_name = self._enqueue_registered_report("prepared-report-test")
		frappe.set_user(self.users[0])
		with patch("yrp.web_reports.jobs.generate_report_result", side_effect=slow_result):
			execute_report_job(job_name)

		job = frappe.get_doc("YRP Report Job", job_name)
		self.assertEqual(job.status, "Completed", job.error_message)
		self.assertEqual(observed, [(1, self.users[0])])
		self.assertEqual(frappe.db.get_value("Report", report_name, "prepared_report"), 0)

	def test_failing_test_report_records_error_and_done_event(self):
		frappe.set_user(self.users[0])
		with (
			patch("yrp.web_reports.jobs.frappe.enqueue"),
			patch("yrp.web_reports.jobs.get_workers", return_value=[object()]),
		):
			response = run_web_report("web-report--test-failing", {})
		job_name = response["job"]["name"]
		with patch("yrp.web_reports.jobs.frappe.publish_realtime") as publish:
			execute_report_job(job_name)

		job = frappe.get_doc("YRP Report Job", job_name)
		self.assertEqual((job.status, job.error_kind), ("Error", "invalid"))
		self.assertEqual(job.error_message, "Intentional web report test failure")
		envelope = get_report_job("web-report--test-failing", job_name)
		self.assertEqual(envelope["message"], "Intentional web report test failure")
		publish.assert_called_with(
			"yrp_report_done",
			{"job": job_name, "status": "Error"},
			user=self.users[0],
		)

	def test_scheduler_cleanup_removes_old_job_owned_by_another_user(self):
		job_name = self._enqueue(self.users[1])["job"]["name"]
		frappe.db.set_value(
			"YRP Report Job",
			job_name,
			"creation",
			add_to_date(now_datetime(), days=-15),
			update_modified=False,
		)
		frappe.db.commit()

		frappe.set_user("Administrator")
		clear_old_jobs()

		self.assertFalse(frappe.db.exists("YRP Report Job", job_name))

	def test_cleanup_commits_each_success_and_continues_after_a_failure(self):
		first = self._complete_job()
		second = self._enqueue(refresh=1)["job"]["name"]
		frappe.set_user(self.users[0])
		execute_report_job(second)
		third = self._enqueue(refresh=1)["job"]["name"]
		execute_report_job(third)
		frappe.set_user("Administrator")
		for index, job_name in enumerate((first, second, third), start=15):
			frappe.db.set_value(
				"YRP Report Job",
				job_name,
				"creation",
				add_to_date(now_datetime(), days=-index),
				update_modified=False,
			)
		frappe.db.commit()

		from frappe.model import delete_doc as delete_doc_module

		original_remove_all = delete_doc_module.remove_all

		def fail_first_attachment_delete(doctype, name, **kwargs):
			if doctype == "YRP Report Job" and name == second:
				raise RuntimeError("forced attachment delete failure")
			return original_remove_all(doctype, name, **kwargs)

		original_get_all = frappe.get_all

		def select_cleanup_jobs(doctype, *args, **kwargs):
			if doctype == "YRP Report Job":
				return [first, second, third]
			return original_get_all(doctype, *args, **kwargs)

		with (
			patch("yrp.web_reports.cleanup.frappe.get_all", side_effect=select_cleanup_jobs),
			patch("frappe.model.delete_doc.remove_all", side_effect=fail_first_attachment_delete),
			patch("yrp.web_reports.cleanup.frappe.log_error") as log_error,
		):
			clear_old_jobs()

		self.assertFalse(frappe.db.exists("YRP Report Job", first))
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": first}))
		self.assertTrue(frappe.db.exists("YRP Report Job", second))
		self.assertTrue(frappe.db.exists("File", {"attached_to_name": second}))
		self.assertFalse(frappe.db.exists("YRP Report Job", third))
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": third}))
		log_error.assert_called_once()

	def test_clear_old_logs_deletes_selected_job_and_attachment(self):
		with patch("yrp.web_reports.cleanup.clear_old_jobs") as clear_old_jobs_for_retention:
			YRPReportJob.clear_old_logs(9)
		clear_old_jobs_for_retention.assert_called_once_with(days=9)

		job_name = self._complete_job()
		original_get_all = frappe.get_all

		def select_job(doctype, *args, **kwargs):
			if doctype == "YRP Report Job":
				return [job_name]
			return original_get_all(doctype, *args, **kwargs)

		with patch(
			"yrp.web_reports.cleanup.frappe.get_all",
			side_effect=select_job,
		):
			YRPReportJob.clear_old_logs(0)

		self.assertFalse(frappe.db.exists("YRP Report Job", job_name))
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": job_name}))

	def test_lazy_cleanup_is_hourly_and_enqueue_failures_do_not_block_reports(self):
		cache_key = frappe.cache.make_key("yrp_report_cleanup_hourly")
		original_cache_set = frappe.cache.set
		responses = iter((True, False))

		def cache_once(key, *args, **kwargs):
			if key == cache_key:
				return next(responses)
			return original_cache_set(key, *args, **kwargs)

		with (
			patch("yrp.web_reports.jobs.frappe.cache.set", side_effect=cache_once),
			patch("yrp.web_reports.jobs.frappe.enqueue") as enqueue,
		):
			_enqueue_lazy_cleanup()
			_enqueue_lazy_cleanup()
		enqueue.assert_called_once_with(
			"yrp.web_reports.cleanup.clear_old_jobs",
			queue="short",
			job_id="yrp_report_cleanup",
			deduplicate=True,
			limit=50,
		)

		with (
			patch(
				"yrp.web_reports.jobs.frappe.cache.set",
				side_effect=lambda key, *args, **kwargs: (
					True if key == cache_key else original_cache_set(key, *args, **kwargs)
				),
			),
			patch("yrp.web_reports.jobs.frappe.enqueue", side_effect=RuntimeError("queue unavailable")),
			patch("yrp.web_reports.jobs.frappe.log_error") as log_error,
		):
			_enqueue_lazy_cleanup()
		log_error.assert_called_once()

		frappe.set_user(self.users[0])
		with (
			patch(
				"yrp.web_reports.jobs.frappe.cache.set",
				side_effect=lambda key, *args, **kwargs: (
					True if key == cache_key else original_cache_set(key, *args, **kwargs)
				),
			),
			patch("yrp.web_reports.jobs.frappe.enqueue", side_effect=RuntimeError("queue unavailable")),
			patch("yrp.web_reports.jobs.frappe.log_error"),
		):
			response = run_web_report("stock-balance--test-never", self.stock_filters)
		self.assertEqual(response["mode"], "sync")

	def test_stock_balance_sync_and_background_payloads_match(self):
		frappe.set_user(self.users[0])
		sync = run_web_report("stock-balance--test-never", self.stock_filters)
		with (
			patch("yrp.web_reports.jobs.frappe.enqueue"),
			patch("yrp.web_reports.jobs.get_workers", return_value=[object()]),
		):
			background = run_web_report("stock-balance--test-always", self.stock_filters)
		execute_report_job(background["job"]["name"])
		stored = get_report_job("stock-balance--test-always", background["job"]["name"], include_result=1)

		self.assertEqual(sync["mode"], "sync")
		self.assertEqual(background["mode"], "background")
		self.assertEqual(_business_payload(sync), _business_payload(stored))

	def test_unknown_http_argument_cannot_force_background(self):
		from yrp.api.web_reports import run_web_report as api_run_web_report

		frappe.set_user(self.users[0])
		response = frappe.call(
			api_run_web_report,
			report_key="stock-balance--test-never",
			filters=self.stock_filters,
			mode="background",
			other_argument="ignored",
		)

		self.assertEqual(response["mode"], "sync")
		self.assertFalse(frappe.db.exists("YRP Report Job", {"report_key": "stock-balance--test-never"}))

	def _create_role(self):
		role_name = f"Web Report Test {frappe.generate_hash(length=8)}"
		frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)
		self.fixture_records.append(("Role", role_name))
		return role_name

	def _create_custom_docperm(self, role, permlevel):
		permission = frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "Item",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": permlevel,
				"read": 1,
			}
		)
		permission.db_insert()
		self.fixture_records.append(("Custom DocPerm", permission.name))
		return permission.name

	def _create_report_fixture(self):
		role = self._create_role()
		frappe.get_doc("User", self.users[0]).add_roles(role)
		self.assigned_roles.append((self.users[0], role))
		report_name = f"Web Report Security {frappe.generate_hash(length=8)}"
		report = frappe.get_doc(
			{
				"doctype": "Report",
				"name": report_name,
				"report_name": report_name,
				"ref_doctype": "YRP Stock Ledger Entry",
				"report_type": "Query Report",
				"is_standard": "No",
				"query": "select name from `tabYRP Stock Ledger Entry` limit 1",
				"prepared_report": 0,
				"disabled": 0,
			}
		)
		report.db_insert()
		for report_role in (role, "System Manager"):
			frappe.get_doc(
				{
					"doctype": "Has Role",
					"parent": report_name,
					"parenttype": "Report",
					"parentfield": "roles",
					"role": report_role,
				}
			).db_insert()
		self.fixture_records.append(("Report", report_name))
		return report_name, role

	def _register_frappe_report_entry(self, key, report_name):
		entry = replace(
			get_web_report("stock-balance"),
			key=key,
			report=report_name,
			permission_doctypes=("YRP Stock Ledger Entry",),
			dependency_doctypes=None,
			background="always",
		)
		frappe.flags.yrp_web_report_overrides[key] = entry
		clear_web_report_cache()

	def _enqueue_registered_report(self, key):
		frappe.set_user(self.users[0])
		with (
			patch("yrp.web_reports.jobs.frappe.enqueue"),
			patch("yrp.web_reports.jobs.get_workers", return_value=[object()]),
		):
			return run_web_report(key, self.stock_filters)["job"]["name"]

	def _complete_registered_report(self, key):
		job_name = self._enqueue_registered_report(key)
		frappe.set_user(self.users[0])
		with patch(
			"yrp.web_reports.jobs.generate_report_result",
			return_value={"columns": [], "result": [{"name": self.item}]},
		):
			execute_report_job(job_name)
		job = frappe.get_doc("YRP Report Job", job_name)
		self.assertEqual(job.status, "Completed", job.error_message)
		return job_name

	def _delete_fixture_records(self):
		for user, role in reversed(self.assigned_roles):
			if frappe.db.exists("User", user) and frappe.db.exists("Role", role):
				frappe.get_doc("User", user).remove_roles(role)
		for doctype, name in reversed(self.fixture_records):
			if doctype == "Report":
				frappe.db.delete("Has Role", {"parent": name, "parenttype": "Report"})
				frappe.db.delete("Report", {"name": name})
			elif frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)

	def _enqueue(self, user=None, refresh=0):
		frappe.set_user(user or self.users[0])
		with (
			patch("yrp.web_reports.jobs.frappe.enqueue"),
			patch("yrp.web_reports.jobs.get_workers", return_value=[object()]),
		):
			return run_web_report("stock-balance-job-test", self.stock_filters, refresh=refresh)

	def _complete_job(self):
		response = self._enqueue()
		job_name = response["job"]["name"]
		frappe.set_user(self.users[0])
		execute_report_job(job_name)
		job = frappe.get_doc("YRP Report Job", job_name)
		self.assertEqual(job.status, "Completed", job.error_message)
		return job_name

	@property
	def stock_filters(self):
		return {
			"from_date": frappe.utils.today(),
			"to_date": frappe.utils.today(),
			"item": self.item,
			"warehouse": self.warehouse,
		}


def _stock_filters():
	return {"from_date": frappe.utils.today(), "to_date": frappe.utils.today()}


class _StorageNotFoundError(Exception):
	def __init__(self, code):
		super().__init__(code)
		self.response = {"Error": {"Code": code}}


def _commit_user_permission(site, sites_path, user, item, created_permissions):
	frappe.init(site, sites_path=sites_path)
	frappe.connect()
	frappe.set_user("Administrator")
	try:
		permission = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": "Item",
				"for_value": item,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		created_permissions.append(permission.name)
	finally:
		frappe.destroy()


def _make_user(prefix, roles=("Stock User",)):
	email = f"{prefix}-{frappe.generate_hash(length=8)}@example.test"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Report Job Test",
			"enabled": 1,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)
	if roles:
		user.add_roles(*roles)
	return email


def _delete_fixture_user(user):
	for attempt in range(3):
		try:
			if frappe.db.exists("User", user):
				frappe.delete_doc("User", user, ignore_permissions=True, force=True)
			frappe.db.commit()
			return
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt == 2:
				raise
			time.sleep(0.1)


def _rest_get_status(user, doctype, name):
	from frappe.core.doctype.user.user import generate_keys

	frappe.set_user("Administrator")
	credentials = generate_keys(user)
	api_key = frappe.db.get_value("User", user, "api_key")
	frappe.db.commit()
	client = get_test_client(use_cookies=False)
	path = f"/api/resource/{quote(doctype)}/{quote(name)}"
	response = make_request(
		target=client.get,
		args=(path,),
		kwargs={"headers": {"Authorization": f"token {api_key}:{credentials['api_secret']}"}},
	)
	return response.status_code


def _make_physical_item():
	item_code = f"_Test Web Report Item {frappe.generate_hash(length=8)}"
	stock_uom = frappe.db.get_value("UOM", {}, "name")
	if not stock_uom:
		raise RuntimeError("needs one UOM")
	return frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_code,
			"item_group": _item_group(),
			"stock_uom": stock_uom,
			"is_stock_item": 1,
			"is_sales_item": 0,
			**_india_compliance_item_fields(),
		}
	).insert(ignore_permissions=True).name


def _insert_ledger(item, warehouse):
	dimensions = {}
	for dimension in get_stock_dimensions():
		value = frappe.db.get_value(dimension["dimension_doctype"], {}, "name")
		if dimension.get("mandatory") and not value:
			raise RuntimeError(f"needs one {dimension['dimension_doctype']}")
		dimensions[dimension["fieldname"]] = value
	doc = frappe.get_doc(
		{
			"doctype": "YRP Stock Ledger Entry",
			"item": item,
			"warehouse": warehouse,
			"posting_date": frappe.utils.today(),
			"posting_time": frappe.utils.nowtime(),
			"voucher_type": "Item",
			"voucher_no": f"WEB-JOB-{frappe.generate_hash(length=8)}",
			"qty": 17,
			"qty_after_transaction": 17,
			"rate": 3,
			"valuation_rate": 3,
			"stock_value": 51,
			"stock_value_difference": 51,
			**dimensions,
		}
	).insert(ignore_permissions=True, ignore_links=True)
	return doc.name


def _job_file(job_name):
	name = frappe.db.get_value(
		"File",
		{"attached_to_doctype": "YRP Report Job", "attached_to_name": job_name},
		"name",
	)
	return frappe.get_doc("File", name)


def _delete_jobs():
	job_names = frappe.get_all(
		"YRP Report Job",
		filters={
			"report_key": (
				"in",
				[
					"stock-balance-job-test",
					"stock-balance--test-never",
					"stock-balance--test-always",
					"web-report--test-failing",
					"security-report-test",
					"incomplete-dependency-test",
					"prepared-report-test",
				],
			)
		},
		pluck="name",
	)
	for file_name in frappe.get_all(
		"File",
		filters={"attached_to_doctype": "YRP Report Job", "attached_to_name": ("in", job_names)},
		pluck="name",
	):
		frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
	for job_name in job_names:
		frappe.delete_doc("YRP Report Job", job_name, ignore_permissions=True, force=True)
	frappe.db.commit()


def _business_payload(payload):
	return {
		key: payload.get(key)
		for key in ("columns", "result", "report_summary", "message")
	}
