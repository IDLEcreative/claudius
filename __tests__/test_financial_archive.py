"""No credentials or provider mutations: exercise the real filing boundary."""

import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from lib.financial_archive import archive_files, folder_contents, MAX_ATTACHMENT_BYTES
from lib.financial_attachments import financial_attachments


def part(name="invoice.pdf", content_id=None, disposition=None):
    headers = []
    if content_id:
        headers.append({"name": "Content-ID", "value": content_id})
    if disposition:
        headers.append({"name": "Content-Disposition", "value": disposition})
    return {"filename": name, "mimeType": "application/pdf", "headers": headers,
            "body": {"attachmentId": name, "size": 10}}


def message(subject, parts):
    return {"payload": {"headers": [{"name": "Subject", "value": subject}], "parts": parts}}


class SelectionTests(unittest.TestCase):
    def test_analytics_vendor_pdf_is_not_a_receipt(self):
        self.assertEqual(financial_attachments(message("Google Analytics report", [part("report.pdf")])), [])

    def test_signature_is_not_a_receipt(self):
        files = [part("invoice.pdf"), part("logo.png"), part("image001.png", "cid"),
                 part("receipt.jpg", "cid")]
        self.assertEqual([a["filename"] for a in financial_attachments(message("Your invoice", files))],
                         ["invoice.pdf", "receipt.jpg"])

    def test_scanned_receipt_is_retained(self):
        self.assertEqual(len(financial_attachments(message("Receipt attached", [part("IMG123.jpg")]))), 1)

    def test_vip_generic_file_not_financial(self):
        self.assertEqual(financial_attachments(message("Project artwork", [part("creative.pdf")])), [])

    def test_named_invoice_with_generic_subject_and_nested_parts(self):
        self.assertEqual(len(financial_attachments(message("As discussed", [{"parts": [part()]}]))), 1)

    def test_single_part_root_is_captured(self):
        self.assertEqual(len(financial_attachments({"payload": part()})), 1)


class FilingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lock = str(Path(self.temp.name) / "account.lock")
        self.data = b"invoice one"
        self.files = {}
        self.upload_count = 0
        self.read_count = 0
        self.patch = patch("lib.financial_archive._read", self.read)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def metadata(self, data):
        return {"md5Checksum": hashlib.md5(data).hexdigest(), "size": str(len(data)),
                "parents": ["quarter"], "mimeType": "application/pdf"}

    def read(self, endpoint, token):
        self.read_count += 1
        if endpoint.startswith("files?"):
            return {"files": list(self.files.values())}
        return self.files[endpoint.split("?")[0].split("/")[1]]

    def upload(self, data, name, folder, mime):
        self.upload_count += 1
        file_id = str(self.upload_count)
        self.files[file_id] = self.metadata(data)
        return file_id

    def run_archive(self, attachments=None, download=None, upload=None, folder="quarter"):
        return archive_files(attachments or [{"filename": "invoice.pdf", "size": 10,
                             "mime_type": "application/pdf"}], folder, self.lock,
                             lambda: "fake-token", download or (lambda _: self.data),
                             upload or self.upload, lambda a: a["filename"])

    def test_same_filename_different_contents_both_saved(self):
        self.run_archive()
        self.data = b"invoice two"
        self.assertEqual(self.run_archive()["saved"], ["invoice.pdf"])
        self.assertEqual(self.upload_count, 2)

    def test_reminder_same_contents_different_name_skipped(self):
        self.run_archive()
        result = self.run_archive([{"filename": "reminder.pdf", "mime_type": "application/pdf"}])
        self.assertEqual(result["duplicate"], ["reminder.pdf"])
        self.assertEqual(self.upload_count, 1)

    def test_legacy_drive_checksum_prevents_duplicate(self):
        self.files["legacy"] = self.metadata(self.data)
        self.assertTrue(self.run_archive()["duplicate"])
        self.assertEqual(self.upload_count, 0)

    def test_two_collectors_share_one_lock(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.run_archive(), range(2)))
        self.assertEqual(sum(len(r["saved"]) for r in results), 1)
        self.assertEqual(self.upload_count, 1)

    def test_list_failure_never_uploads(self):
        with patch("lib.financial_archive._read", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                self.run_archive()
        self.assertEqual(self.upload_count, 0)

    def test_empty_response_is_not_empty_folder(self):
        with patch("lib.financial_archive._read", return_value={}):
            with self.assertRaises(ValueError):
                self.run_archive()
        self.assertEqual(self.upload_count, 0)

    def test_failed_download_and_upload_are_not_success(self):
        self.assertTrue(self.run_archive(download=lambda _: None)["failed"])
        self.assertTrue(self.run_archive(upload=lambda *args: None)["failed"])

    def test_readback_mismatch_is_not_success(self):
        def wrong_upload(*args):
            file_id = self.upload(*args)
            self.files[file_id]["parents"] = ["wrong-account"]
            return file_id
        result = self.run_archive(upload=wrong_upload)
        self.assertTrue(result["failed"])
        self.assertFalse(result["saved"])

    def test_unknown_upload_outcome_is_recovered_without_duplicate(self):
        def interrupted_upload(*args):
            self.upload(*args)
            raise TimeoutError("lost response")
        self.assertTrue(self.run_archive(upload=interrupted_upload)["failed"])
        self.assertTrue(self.run_archive()["duplicate"])
        self.assertEqual(self.upload_count, 1)

    def test_missing_folder_does_not_upload(self):
        self.assertTrue(self.run_archive(folder="")["failed"])
        self.assertEqual(self.read_count, 0)

    def test_size_limit_checked_before_download(self):
        download = Mock()
        self.run_archive([{"filename": "big.pdf", "size": MAX_ATTACHMENT_BYTES + 1}], download=download)
        download.assert_not_called()

    def test_pagination_growth_is_linear_and_capped(self):
        for count in (10, 100):
            with self.subTest(count=count):
                rows = [{"md5Checksum": str(i), "size": "1"} for i in range(count)]
                pages = [{"files": rows[:count // 2], "nextPageToken": "next"},
                         {"files": rows[count // 2:]}]
                with patch("lib.financial_archive._read", side_effect=pages) as read:
                    self.assertEqual(len(folder_contents("quarter", "token")), count)
                    self.assertEqual(read.call_count, 2)
        with patch("lib.financial_archive._read", return_value={"files": [], "nextPageToken": "more"}) as read:
            with self.assertRaises(ValueError):
                folder_contents("quarter", "token")
            self.assertEqual(read.call_count, 20)


if __name__ == "__main__":
    unittest.main()
