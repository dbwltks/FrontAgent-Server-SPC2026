import unittest
from unittest.mock import MagicMock, patch

from app.repositories.knowledge_storage import (
    build_knowledge_storage_path,
    content_type_for_file,
    create_knowledge_download_url,
    upload_knowledge_original,
)


class KnowledgeStorageTests(unittest.TestCase):
    def test_storage_path_is_scoped_and_uses_unique_file_name(self):
        storage_path = build_knowledge_storage_path(
            organization_id="org-id",
            source_id="source-id",
            file_name="가격표.PDF",
        )

        self.assertTrue(storage_path.startswith("org-id/source-id/"))
        self.assertTrue(storage_path.endswith(".pdf"))
        self.assertNotIn("가격표", storage_path)

    def test_content_type_is_normalized_from_extension(self):
        self.assertEqual(
            content_type_for_file("document.xlsx", "application/octet-stream"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @patch("app.repositories.knowledge_storage.supabase")
    def test_upload_disables_upsert(self, supabase_mock):
        bucket = MagicMock()
        supabase_mock.storage.from_.return_value = bucket

        with patch("builtins.open", MagicMock()):
            upload_knowledge_original(
                local_file_path="/tmp/source.pdf",
                storage_path="org/source/file.pdf",
                content_type="application/pdf",
            )

        bucket.upload.assert_called_once()
        options = bucket.upload.call_args.kwargs["file_options"]
        self.assertEqual(options["upsert"], "false")
        self.assertEqual(options["content-type"], "application/pdf")

    @patch("app.repositories.knowledge_storage.supabase")
    def test_download_url_uses_private_signed_url(self, supabase_mock):
        bucket = MagicMock()
        bucket.create_signed_url.return_value = {
            "signedURL": "https://example.test/signed",
        }
        supabase_mock.storage.from_.return_value = bucket

        url = create_knowledge_download_url(
            "knowledge-originals",
            "org/source/file.pdf",
        )

        self.assertEqual(url, "https://example.test/signed")
        bucket.create_signed_url.assert_called_once_with(
            "org/source/file.pdf",
            300,
        )


if __name__ == "__main__":
    unittest.main()
