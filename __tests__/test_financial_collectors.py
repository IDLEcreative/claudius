"""Exercise actual collector entrypoints with all external effects replaced."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lib.config import AccountConfig


def load_collector(filename):
    path = Path(__file__).resolve().parents[1] / filename
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    account = AccountConfig("test", "test@example.invalid", "/no-credentials", "root", "test")
    with patch.object(AccountConfig, "load", return_value=account), \
            patch("lib.telegram_sender._ensure_env"):
        spec.loader.exec_module(module)
    return module


def invoice(sender="billing@example.invalid", subject="Invoice", filename="invoice.pdf"):
    return {"id": "message", "payload": {
        "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject},
                    {"name": "Date", "value": "Mon, 03 Aug 2026 10:00:00 +0000"}],
        "parts": [{"filename": filename, "mimeType": "application/pdf",
                   "body": {"attachmentId": "attachment", "size": 10}}]}}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.router = load_collector("email-router.py")
        self.daily = load_collector("invoice-archiver.py")
        self.learned = patch.object(self.router, "is_learned_priority_sender", return_value=False)
        self.learned.start()
        self.addCleanup(self.learned.stop)

    def test_google_report_does_not_reach_drive(self):
        with patch.object(self.router, "action_save_to_drive") as save, \
                patch.object(self.router, "action_telegram_alert", return_value=False):
            self.router.route_email(invoice("noreply@google.com", "Analytics report", "report.pdf"))
            save.assert_not_called()

    def test_vip_artwork_does_not_reach_drive(self):
        with patch.object(self.router, "action_save_to_drive") as save, \
                patch.object(self.router, "action_telegram_alert", return_value=False):
            self.router.route_email(invoice("vip@anthropic.com", "Artwork", "design.pdf"))
            save.assert_not_called()

    def test_partial_upload_does_not_mark_router_message_processed(self):
        with patch.object(self.router, "load_state", return_value={"processed_ids": []}), \
                patch.object(self.router, "search_messages", return_value=[{"id": "message"}]), \
                patch.object(self.router, "get_message", return_value=invoice()), \
                patch.object(self.router, "action_save_to_drive", return_value={"saved": [], "failed": ["invoice.pdf"]}), \
                patch.object(self.router, "save_state") as save:
            self.router.process_new_emails()
            self.assertNotIn("message", save.call_args.args[0]["processed_ids"])

    def test_partial_upload_does_not_mark_daily_message_archived(self):
        with patch.object(self.daily, "load_state", return_value={"archived_message_ids": []}), \
                patch.object(self.daily, "search_messages", return_value=[{"id": "message"}]), \
                patch.object(self.daily, "get_message", return_value=invoice()), \
                patch.object(self.daily, "get_quarterly_folder", return_value=("year", "quarter")), \
                patch.object(self.daily, "archive_files", return_value={"saved": [], "failed": ["invoice.pdf"]}), \
                patch.object(self.daily, "send_telegram") as notify, \
                patch.object(self.daily, "save_state") as save:
            self.daily.archive_receipt_emails()
            self.assertNotIn("message", save.call_args.args[0]["archived_message_ids"])
            notify.assert_not_called()

    def test_both_collectors_use_same_account_lock(self):
        fake = Mock(return_value={"saved": [], "failed": []})
        with patch.object(self.router, "get_quarterly_folder", return_value="quarter"), \
                patch.object(self.router, "archive_files", fake):
            msg = invoice()
            self.router.action_save_to_drive(msg, self.router.get_message_headers(msg), self.router.find_attachments(msg))
        router_lock = fake.call_args.args[2]
        with patch.object(self.daily, "load_state", return_value={"archived_message_ids": []}), \
                patch.object(self.daily, "search_messages", return_value=[{"id": "message"}]), \
                patch.object(self.daily, "get_message", return_value=invoice()), \
                patch.object(self.daily, "get_quarterly_folder", return_value=("year", "quarter")), \
                patch.object(self.daily, "archive_files", fake), \
                patch.object(self.daily, "save_state"):
            self.daily.archive_receipt_emails()
        self.assertEqual(router_lock, fake.call_args.args[2])

    def test_folder_lookup_failure_does_not_create_replacement(self):
        with patch.object(self.daily, "get_access_token", return_value="fake"), \
                patch("urllib.request.urlopen", side_effect=OSError("unavailable")), \
                patch.object(self.daily, "create_folder") as create:
            with self.assertRaises(RuntimeError):
                self.daily.find_or_create_folder("2026", "root")
            create.assert_not_called()
        with patch.object(self.router, "get_access_token", return_value="fake"), \
                patch("urllib.request.urlopen", side_effect=OSError("unavailable")) as request:
            with self.assertRaises(RuntimeError):
                self.router.get_quarterly_folder(self.router.parse_email_date("Mon, 03 Aug 2026 10:00:00 +0000"))
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
