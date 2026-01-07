"""
Tests for Paperless and Firefly III API clients.

These tests use responses library to mock HTTP requests,
validating client behavior without making real API calls.
"""

import pytest
import responses

from paperless_firefly.firefly_client import (
    FireflyClient,
)
from paperless_firefly.paperless_client import (
    PaperlessClient,
    PaperlessDocument,
    PaperlessError,
)
from paperless_firefly.schemas.firefly_payload import (
    FireflyTransactionSplit,
    FireflyTransactionStore,
)


class TestPaperlessClient:
    """Test Paperless-ngx API client."""

    BASE_URL = "http://paperless.test:8000"
    TOKEN = "test-token-12345"

    @responses.activate
    def test_test_connection_success(self):
        """Test connection check succeeds with valid response."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/",
            json={"status": "ok"},
            status=200,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        assert client.test_connection() is True

    @responses.activate
    def test_test_connection_failure(self):
        """Test connection check fails with server error."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/",
            json={"error": "Internal server error"},
            status=500,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        assert client.test_connection() is False

    @responses.activate
    def test_list_documents_basic(self):
        """Test listing documents without filters."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/",
            json={
                "count": 2,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 1,
                        "title": "Receipt 1",
                        "content": "Sample OCR text",
                        "created": "2024-11-18",
                        "added": "2024-11-19T10:00:00Z",
                        "modified": "2024-11-19T10:00:00Z",
                        "correspondent": None,
                        "document_type": None,
                        "tags": [1, 2],
                        "custom_fields": [],
                    },
                    {
                        "id": 2,
                        "title": "Invoice 1",
                        "content": "Invoice OCR text",
                        "created": "2024-11-17",
                        "added": "2024-11-18T08:00:00Z",
                        "modified": "2024-11-18T08:00:00Z",
                        "correspondent": 5,
                        "document_type": 3,
                        "tags": [1],
                        "custom_fields": [],
                    },
                ],
            },
            status=200,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        docs = list(client.list_documents())

        assert len(docs) == 2
        assert docs[0].id == 1
        assert docs[0].title == "Receipt 1"
        assert docs[1].id == 2
        assert docs[1].title == "Invoice 1"

    @responses.activate
    def test_list_documents_with_tag_filter(self):
        """Test listing documents filtered by tag."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/tags/",
            json={
                "count": 1,
                "results": [{"id": 5, "name": "finance/inbox"}],
            },
            status=200,
        )

        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/",
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 10,
                        "title": "Finance Document",
                        "content": "Finance content",
                        "created": "2024-11-18",
                        "added": "2024-11-19T10:00:00Z",
                        "modified": "2024-11-19T10:00:00Z",
                        "tags": [5],
                        "custom_fields": [],
                    },
                ],
            },
            status=200,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        docs = list(client.list_documents(tags=["finance/inbox"]))

        assert len(docs) == 1
        assert docs[0].id == 10

    @responses.activate
    def test_get_document_success(self):
        """Test fetching single document."""
        # Main document request
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/123/",
            json={
                "id": 123,
                "title": "Test Document",
                "content": "Full OCR content here",
                "created": "2024-11-18",
                "added": "2024-11-19T10:00:00Z",
                "modified": "2024-11-19T10:00:00Z",
                "correspondent": 5,
                "document_type": 2,
                "tags": [1, 3],
                "original_file_name": "invoice.pdf",
                "archive_serial_number": 1001,
                "custom_fields": [],
            },
            status=200,
        )

        # Tag name lookups
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/tags/1/",
            json={"id": 1, "name": "finance/inbox"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/tags/3/",
            json={"id": 3, "name": "pending"},
            status=200,
        )

        # Correspondent lookup
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/correspondents/5/",
            json={"id": 5, "name": "Vendor Inc"},
            status=200,
        )

        # Document type lookup
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/document_types/2/",
            json={"id": 2, "name": "Invoice"},
            status=200,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        doc = client.get_document(123)

        assert doc.id == 123
        assert doc.title == "Test Document"
        assert doc.correspondent == "Vendor Inc"
        assert doc.document_type == "Invoice"
        assert "finance/inbox" in doc.tags

    @responses.activate
    def test_get_document_not_found(self):
        """Test fetching non-existent document."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/99999/",
            json={"detail": "Not found."},
            status=404,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)

        with pytest.raises(PaperlessError):
            client.get_document(99999)

    @responses.activate
    def test_get_document_ocr_content(self):
        """Test fetching document includes OCR content."""
        expected_content = "This is the full OCR extracted text from the document."

        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/123/",
            json={
                "id": 123,
                "title": "Test",
                "content": expected_content,
                "created": "2024-11-18",
                "added": "2024-11-19T10:00:00Z",
                "modified": "2024-11-19T10:00:00Z",
                "tags": [],
                "custom_fields": [],
            },
            status=200,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        doc = client.get_document(123)

        assert doc.content == expected_content

    @responses.activate
    def test_download_document(self):
        """Test downloading document original file."""
        content = b"PDF file content here"

        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/123/download/",
            body=content,
            status=200,
            content_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="document_123"'},
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        downloaded, filename = client.download_original(123)

        # Should return file content bytes and filename
        assert downloaded == content
        assert filename == "document_123"

    @responses.activate
    def test_auth_header_sent(self):
        """Test that auth header is included in requests."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/1/",
            json={
                "id": 1,
                "title": "Test",
                "content": "",
                "created": None,
                "added": "",
                "modified": "",
                "tags": [],
                "custom_fields": [],
            },
            status=200,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        client.get_document(1)

        # Check auth header was sent
        assert responses.calls[0].request.headers["Authorization"] == f"Token {self.TOKEN}"

    def test_document_from_api_response(self):
        """Test PaperlessDocument.from_api_response parsing."""
        data = {
            "id": 42,
            "title": "Test Doc",
            "content": "OCR text",
            "created": "2024-11-18",
            "added": "2024-11-19T10:00:00Z",
            "modified": "2024-11-19T10:00:00Z",
            "correspondent": 5,
            "correspondent__name": "Vendor",
            "document_type": 2,
            "document_type__name": "Receipt",
            "tags": [1, 2],
            "tags__name": ["tag1", "tag2"],
            "original_file_name": "receipt.pdf",
            "archive_serial_number": 100,
            "custom_fields": [
                {"field": "amount", "value": "15.99"},
                {"field": "currency", "value": "EUR"},
            ],
        }

        doc = PaperlessDocument.from_api_response(data, self.BASE_URL)

        assert doc.id == 42
        assert doc.title == "Test Doc"
        assert doc.correspondent == "Vendor"
        assert doc.document_type == "Receipt"
        assert doc.tags == ["tag1", "tag2"]
        assert doc.custom_fields["amount"] == "15.99"
        assert doc.download_url == f"{self.BASE_URL}/api/documents/42/download/"


class TestFireflyClient:
    """Test Firefly III API client."""

    BASE_URL = "http://firefly.test:8080"
    TOKEN = "firefly-token-67890"

    @responses.activate
    def test_test_connection_success(self):
        """Test connection check succeeds."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/about",
            json={"data": {"version": "6.0.0", "api_version": "2.0.0"}},
            status=200,
        )

        client = FireflyClient(self.BASE_URL, self.TOKEN)
        assert client.test_connection() is True

    @responses.activate
    def test_test_connection_failure(self):
        """Test connection check fails on error."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/about",
            json={"error": "Unauthorized"},
            status=401,
        )

        client = FireflyClient(self.BASE_URL, self.TOKEN)
        assert client.test_connection() is False

    @responses.activate
    def test_find_by_external_id_exists(self):
        """Test finding existing transaction by external ID."""
        external_id = "paperless:123:abc123:11.48:2024-11-18"

        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/search/transactions",
            json={
                "data": [
                    {
                        "id": "999",
                        "attributes": {
                            "transactions": [
                                {
                                    "external_id": external_id,
                                    "type": "withdrawal",
                                    "date": "2024-11-18",
                                    "amount": "11.48",
                                    "description": "Test transaction",
                                }
                            ]
                        },
                    }
                ],
                "meta": {"pagination": {"total": 1}},
            },
            status=200,
        )

        client = FireflyClient(self.BASE_URL, self.TOKEN)
        result = client.find_by_external_id(external_id)

        assert result is not None
        assert result.id == 999

    @responses.activate
    def test_find_by_external_id_not_found(self):
        """Test finding non-existent transaction."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/search/transactions",
            json={"data": [], "meta": {"pagination": {"total": 0}}},
            status=200,
        )

        client = FireflyClient(self.BASE_URL, self.TOKEN)
        result = client.find_by_external_id("nonexistent:id")

        assert result is None

    @responses.activate
    def test_create_transaction_success(self):
        """Test creating a new transaction."""
        external_id = "paperless:123:abc:11.48:2024-11-18"

        # Mock find_by_external_id to return no existing transaction
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/search/transactions",
            json={"data": [], "meta": {"pagination": {"total": 0}}},
            status=200,
        )

        responses.add(
            responses.POST,
            f"{self.BASE_URL}/api/v1/transactions",
            json={
                "data": {
                    "id": "12345",
                    "attributes": {
                        "transactions": [
                            {
                                "external_id": external_id,
                                "description": "SPAR Purchase",
                                "amount": "11.48",
                            }
                        ]
                    },
                }
            },
            status=200,
        )

        client = FireflyClient(self.BASE_URL, self.TOKEN)

        # Build proper payload with notes (required for audit trail)
        tx = FireflyTransactionSplit(
            type="withdrawal",
            date="2024-11-18",
            amount="11.48",
            description="SPAR Purchase",
            source_name="Checking Account",
            destination_name="SPAR",
            external_id=external_id,
            notes="paperless_document_id=123, source_hash=abc",
        )
        payload = FireflyTransactionStore(transactions=[tx])

        result = client.create_transaction(payload)

        assert result == 12345

    @responses.activate
    def test_create_transaction_skips_duplicate(self):
        """Test creating transaction with duplicate external_id is skipped."""
        external_id = "paperless:123:abc:11.48:2024-11-18"

        # Mock find_by_external_id to return existing transaction
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/search/transactions",
            json={
                "data": [
                    {
                        "id": "999",
                        "attributes": {
                            "transactions": [
                                {
                                    "external_id": external_id,
                                    "type": "withdrawal",
                                    "date": "2024-11-18",
                                    "amount": "11.48",
                                    "description": "Existing",
                                }
                            ]
                        },
                    }
                ],
            },
            status=200,
        )

        client = FireflyClient(self.BASE_URL, self.TOKEN)

        tx = FireflyTransactionSplit(
            type="withdrawal",
            date="2024-11-18",
            amount="11.48",
            description="SPAR Purchase",
            source_name="Checking Account",
            destination_name="SPAR",
            external_id=external_id,
            notes="paperless_document_id=123",
        )
        payload = FireflyTransactionStore(transactions=[tx])

        # With skip_duplicates=True (default), should return existing ID
        result = client.create_transaction(payload)
        assert result == 999

    @responses.activate
    def test_list_accounts(self):
        """Test listing accounts."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/accounts",
            json={
                "data": [
                    {"id": "1", "attributes": {"name": "Checking Account", "type": "asset"}},
                    {"id": "2", "attributes": {"name": "Savings", "type": "asset"}},
                ],
                "meta": {"pagination": {"total": 2}},
            },
            status=200,
        )

        client = FireflyClient(self.BASE_URL, self.TOKEN)
        accounts = list(client.list_accounts())

        assert len(accounts) >= 2

    @responses.activate
    def test_auth_bearer_header_sent(self):
        """Test that Bearer token is included in requests."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/about",
            json={"data": {}},
            status=200,
        )

        client = FireflyClient(self.BASE_URL, self.TOKEN)
        client.test_connection()

        # Firefly uses Bearer token
        assert responses.calls[0].request.headers["Authorization"] == f"Bearer {self.TOKEN}"


class TestPaperlessClientPagination:
    """Test pagination handling in Paperless client."""

    BASE_URL = "http://paperless.test:8000"
    TOKEN = "test-token"

    @responses.activate
    def test_pagination_follows_next(self):
        """Test that client follows pagination links."""
        # Page 1
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/",
            json={
                "count": 3,
                "next": f"{self.BASE_URL}/api/documents/?page=2",
                "previous": None,
                "results": [
                    {
                        "id": 1,
                        "title": "Doc 1",
                        "content": "",
                        "created": None,
                        "added": "",
                        "modified": "",
                        "tags": [],
                        "custom_fields": [],
                    },
                    {
                        "id": 2,
                        "title": "Doc 2",
                        "content": "",
                        "created": None,
                        "added": "",
                        "modified": "",
                        "tags": [],
                        "custom_fields": [],
                    },
                ],
            },
            status=200,
        )

        # Page 2
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/",
            json={
                "count": 3,
                "next": None,
                "previous": f"{self.BASE_URL}/api/documents/?page=1",
                "results": [
                    {
                        "id": 3,
                        "title": "Doc 3",
                        "content": "",
                        "created": None,
                        "added": "",
                        "modified": "",
                        "tags": [],
                        "custom_fields": [],
                    },
                ],
            },
            status=200,
        )

        client = PaperlessClient(self.BASE_URL, self.TOKEN)
        docs = list(client.list_documents())

        assert len(docs) == 3
        assert [d.id for d in docs] == [1, 2, 3]


class TestClientErrorHandling:
    """Test error handling in clients."""

    @responses.activate
    def test_paperless_api_error(self):
        """Test handling of API errors."""
        from paperless_firefly.paperless_client.client import PaperlessAPIError

        responses.add(
            responses.GET,
            "http://paperless.test:8000/api/documents/1/",
            json={"detail": "Not found."},
            status=404,
        )

        client = PaperlessClient("http://paperless.test:8000", "token")

        with pytest.raises(PaperlessAPIError) as exc_info:
            client.get_document(1)

        assert exc_info.value.status_code == 404

    @responses.activate
    def test_firefly_validation_error(self):
        """Test handling of validation errors from Firefly."""
        from paperless_firefly.firefly_client.client import FireflyAPIError

        # Mock find_by_external_id to return no existing transaction
        responses.add(
            responses.GET,
            "http://firefly.test:8080/api/v1/search/transactions",
            json={"data": [], "meta": {"pagination": {"total": 0}}},
            status=200,
        )

        responses.add(
            responses.POST,
            "http://firefly.test:8080/api/v1/transactions",
            json={
                "message": "The given data was invalid.",
                "errors": {
                    "transactions.0.amount": ["Amount is required"],
                    "transactions.0.date": ["Date format invalid"],
                },
            },
            status=422,
        )

        client = FireflyClient("http://firefly.test:8080", "token")

        tx = FireflyTransactionSplit(
            type="withdrawal",
            date="2024-11-18",
            amount="10.00",
            description="Test",
            external_id="test:123",
            notes="paperless_document_id=123",
        )
        payload = FireflyTransactionStore(transactions=[tx])

        with pytest.raises(FireflyAPIError) as exc_info:
            client.create_transaction(payload)

        assert exc_info.value.status_code == 422


class TestNormalizeTags:
    """Tests for _normalize_tags() SSOT function.

    Firefly III returns tags in varying formats:
    - list[str]: ["groceries", "rent"]
    - list[dict]: [{"tag": "groceries"}, {"tag": "rent"}]
    - Mixed: [{"tag": "groceries"}, "rent", None]
    - None: null

    The normalizer must handle all these robustly.
    """

    def test_case_a_list_of_strings(self):
        """Case A: tags=[\"groceries\",\"rent\"] → same list."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags(["groceries", "rent"])
        assert result == ["groceries", "rent"]

    def test_case_b_list_of_dicts_with_tag_key(self):
        """Case B: tags=[{\"tag\":\"groceries\"},{\"tag\":\"rent\"}] → list[str]."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags([{"tag": "groceries"}, {"tag": "rent"}])
        assert result == ["groceries", "rent"]

    def test_case_c_mixed_list(self):
        """Case C: tags=[{\"tag\":\"groceries\"},\"rent\",None,{\"foo\":\"bar\"}] → [\"groceries\",\"rent\"]."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags([{"tag": "groceries"}, "rent", None, {"foo": "bar"}])
        assert result == ["groceries", "rent"]

    def test_case_d_none_returns_none(self):
        """Case D: tags=None → None."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags(None)
        assert result is None

    def test_empty_list_returns_none(self):
        """Empty list [] → None (treat empty as absent)."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags([])
        assert result is None

    def test_list_with_only_none_returns_none(self):
        """[None, None] → None."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags([None, None])
        assert result is None

    def test_dict_with_name_key(self):
        """Alternate key \"name\" should work: [{\"name\":\"groceries\"}] → [\"groceries\"]."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags([{"name": "groceries"}, {"name": "rent"}])
        assert result == ["groceries", "rent"]

    def test_dict_with_tag_preferred_over_name(self):
        """'tag' key takes precedence over 'name'."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags([{"tag": "preferred", "name": "fallback"}])
        assert result == ["preferred"]

    def test_empty_strings_filtered(self):
        """Empty strings should be filtered out."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        result = _normalize_tags(["groceries", "", "rent", ""])
        assert result == ["groceries", "rent"]

    def test_unexpected_type_raises_error(self):
        """Non-list, non-None raises FireflyAPIError."""
        from paperless_firefly.firefly_client.client import (
            FireflyAPIError,
            _normalize_tags,
        )

        with pytest.raises(FireflyAPIError) as exc_info:
            _normalize_tags({"tag": "invalid"})  # dict, not list
        assert "expected list or None" in str(exc_info.value)

        with pytest.raises(FireflyAPIError):
            _normalize_tags(42)  # int

        with pytest.raises(FireflyAPIError):
            _normalize_tags("groceries")  # string, not list

    def test_complex_mixed_scenario(self):
        """Complex scenario with all variations."""
        from paperless_firefly.firefly_client.client import _normalize_tags

        raw = [
            "direct_string",
            {"tag": "from_tag_key"},
            {"name": "from_name_key"},
            None,
            "",
            {"unknown": "ignored"},
            {"tag": ""},  # empty tag value
            {"name": ""},  # empty name value
        ]
        result = _normalize_tags(raw)
        assert result == ["direct_string", "from_tag_key", "from_name_key"]
