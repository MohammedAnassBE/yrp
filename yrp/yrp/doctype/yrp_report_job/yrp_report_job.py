from frappe.model.document import Document


class YRPReportJob(Document):
	@staticmethod
	def clear_old_logs(days=14):
		from yrp.web_reports.cleanup import clear_old_jobs

		clear_old_jobs(days=days)
