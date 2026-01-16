"""
Tests for Firefly Sync Assistant functionality.

Covers:
- Fingerprint computation
- Pool record management
- Share permissions
- Import logic
"""

import hashlib
import json

import pytest


class TestSyncFingerprints:
    """Tests for fingerprint computation utilities."""

    def test_category_fingerprint_stable(self):
        """Same input produces same fingerprint."""
        from paperless_firefly.services.sync_fingerprints import compute_category_fingerprint

        data1 = {"name": "Groceries"}
        data2 = {"name": "Groceries"}

        assert compute_category_fingerprint(data1) == compute_category_fingerprint(data2)

    def test_category_fingerprint_normalized(self):
        """Fingerprint is case-insensitive and strips whitespace."""
        from paperless_firefly.services.sync_fingerprints import compute_category_fingerprint

        data1 = {"name": "Groceries"}
        data2 = {"name": "groceries "}
        data3 = {"name": "  GROCERIES  "}

        fp1 = compute_category_fingerprint(data1)
        fp2 = compute_category_fingerprint(data2)
        fp3 = compute_category_fingerprint(data3)

        assert fp1 == fp2 == fp3

    def test_category_fingerprint_different_names(self):
        """Different names produce different fingerprints."""
        from paperless_firefly.services.sync_fingerprints import compute_category_fingerprint

        data1 = {"name": "Groceries"}
        data2 = {"name": "Transportation"}

        assert compute_category_fingerprint(data1) != compute_category_fingerprint(data2)

    def test_category_fingerprint_ignores_notes(self):
        """Fingerprint ignores notes field."""
        from paperless_firefly.services.sync_fingerprints import compute_category_fingerprint

        data1 = {"name": "Groceries", "notes": "For food shopping"}
        data2 = {"name": "Groceries", "notes": "Different notes"}
        data3 = {"name": "Groceries"}

        fp1 = compute_category_fingerprint(data1)
        fp2 = compute_category_fingerprint(data2)
        fp3 = compute_category_fingerprint(data3)

        assert fp1 == fp2 == fp3

    def test_category_fingerprint_requires_name(self):
        """Fingerprint raises error if name is empty."""
        from paperless_firefly.services.sync_fingerprints import compute_category_fingerprint

        with pytest.raises(ValueError, match="Category must have a name"):
            compute_category_fingerprint({"name": ""})

        with pytest.raises(ValueError, match="Category must have a name"):
            compute_category_fingerprint({})

    def test_tag_fingerprint_stable(self):
        """Tag fingerprint is stable."""
        from paperless_firefly.services.sync_fingerprints import compute_tag_fingerprint

        data1 = {"tag": "groceries"}
        data2 = {"tag": "groceries"}

        assert compute_tag_fingerprint(data1) == compute_tag_fingerprint(data2)

    def test_tag_fingerprint_uses_tag_field(self):
        """Tag fingerprint uses 'tag' field, not 'name'."""
        from paperless_firefly.services.sync_fingerprints import compute_tag_fingerprint

        data_with_tag = {"tag": "groceries"}
        data_with_name = {"name": "groceries"}  # Fallback

        fp1 = compute_tag_fingerprint(data_with_tag)
        fp2 = compute_tag_fingerprint(data_with_name)

        # Both should produce same fingerprint (name is fallback)
        assert fp1 == fp2

    def test_account_fingerprint_includes_type(self):
        """Account fingerprint includes account type."""
        from paperless_firefly.services.sync_fingerprints import compute_account_fingerprint

        data1 = {"name": "Checking", "type": "asset", "currency_code": "EUR"}
        data2 = {"name": "Checking", "type": "expense", "currency_code": "EUR"}

        assert compute_account_fingerprint(data1) != compute_account_fingerprint(data2)

    def test_account_fingerprint_includes_currency(self):
        """Account fingerprint includes currency code."""
        from paperless_firefly.services.sync_fingerprints import compute_account_fingerprint

        data1 = {"name": "Checking", "type": "asset", "currency_code": "EUR"}
        data2 = {"name": "Checking", "type": "asset", "currency_code": "USD"}

        assert compute_account_fingerprint(data1) != compute_account_fingerprint(data2)

    def test_account_fingerprint_defaults_currency(self):
        """Account fingerprint defaults to EUR if no currency specified."""
        from paperless_firefly.services.sync_fingerprints import compute_account_fingerprint

        data1 = {"name": "Checking", "type": "asset", "currency_code": "EUR"}
        data2 = {"name": "Checking", "type": "asset"}  # No currency

        assert compute_account_fingerprint(data1) == compute_account_fingerprint(data2)

    def test_piggy_bank_fingerprint_includes_target(self):
        """Piggy bank fingerprint includes target amount."""
        from paperless_firefly.services.sync_fingerprints import compute_piggy_bank_fingerprint

        data1 = {"name": "Vacation", "target_amount": "1000.00"}
        data2 = {"name": "Vacation", "target_amount": "2000.00"}

        assert compute_piggy_bank_fingerprint(data1) != compute_piggy_bank_fingerprint(data2)

    def test_piggy_bank_fingerprint_normalizes_amount(self):
        """Piggy bank fingerprint normalizes target amount to 2 decimals."""
        from paperless_firefly.services.sync_fingerprints import compute_piggy_bank_fingerprint

        data1 = {"name": "Vacation", "target_amount": "1000"}
        data2 = {"name": "Vacation", "target_amount": "1000.00"}
        data3 = {"name": "Vacation", "target_amount": 1000}

        fp1 = compute_piggy_bank_fingerprint(data1)
        fp2 = compute_piggy_bank_fingerprint(data2)
        fp3 = compute_piggy_bank_fingerprint(data3)

        assert fp1 == fp2 == fp3

    def test_compute_fingerprint_registry(self):
        """Generic compute_fingerprint uses registry."""
        from paperless_firefly.services.sync_fingerprints import compute_fingerprint

        category_data = {"name": "Test"}
        tag_data = {"tag": "test"}
        account_data = {"name": "Test", "type": "asset", "currency_code": "EUR"}
        piggy_data = {"name": "Test", "target_amount": "100"}

        # All should succeed
        assert len(compute_fingerprint("category", category_data)) == 64
        assert len(compute_fingerprint("tag", tag_data)) == 64
        assert len(compute_fingerprint("account", account_data)) == 64
        assert len(compute_fingerprint("piggy_bank", piggy_data)) == 64

    def test_compute_fingerprint_invalid_type(self):
        """Generic compute_fingerprint raises for invalid type."""
        from paperless_firefly.services.sync_fingerprints import compute_fingerprint

        with pytest.raises(ValueError, match="Unsupported entity type"):
            compute_fingerprint("invalid_type", {"name": "Test"})


class TestNormalizeEntityData:
    """Tests for entity data normalization."""

    def test_normalize_category(self):
        """Normalize category extracts name and notes."""
        from paperless_firefly.services.sync_fingerprints import normalize_entity_data

        raw = {"name": "Groceries", "notes": "Food", "extra_field": "ignored"}
        normalized = normalize_entity_data("category", raw)

        assert normalized == {"name": "Groceries", "notes": "Food"}

    def test_normalize_tag(self):
        """Normalize tag extracts tag and description."""
        from paperless_firefly.services.sync_fingerprints import normalize_entity_data

        raw = {"tag": "groceries", "description": "For food", "extra": "ignored"}
        normalized = normalize_entity_data("tag", raw)

        assert normalized == {"tag": "groceries", "description": "For food"}

    def test_normalize_account(self):
        """Normalize account extracts relevant fields."""
        from paperless_firefly.services.sync_fingerprints import normalize_entity_data

        raw = {
            "name": "Checking",
            "type": "asset",
            "currency_code": "EUR",
            "notes": "Main account",
            "iban": "DE89...",
            "account_number": "12345",
            "opening_balance": "1000",  # Should be ignored
        }
        normalized = normalize_entity_data("account", raw)

        assert normalized == {
            "name": "Checking",
            "type": "asset",
            "currency_code": "EUR",
            "notes": "Main account",
            "iban": "DE89...",
            "account_number": "12345",
        }

    def test_normalize_piggy_bank(self):
        """Normalize piggy bank extracts relevant fields."""
        from paperless_firefly.services.sync_fingerprints import normalize_entity_data

        raw = {
            "name": "Vacation",
            "target_amount": "1000",
            "current_amount": "500",
            "notes": "Summer trip",
            "account_id": 5,  # Should be ignored
        }
        normalized = normalize_entity_data("piggy_bank", raw)

        assert normalized == {
            "name": "Vacation",
            "target_amount": "1000",
            "current_amount": "500",
            "notes": "Summer trip",
        }


class TestGetEntityName:
    """Tests for entity name extraction."""

    def test_get_name_category(self):
        """Get name from category."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        assert get_entity_name("category", {"name": "Groceries"}) == "Groceries"

    def test_get_name_tag(self):
        """Get name from tag (uses 'tag' field)."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        assert get_entity_name("tag", {"tag": "groceries"}) == "groceries"

    def test_get_name_account(self):
        """Get name from account."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        assert get_entity_name("account", {"name": "Checking"}) == "Checking"

    def test_get_name_fallback(self):
        """Get name falls back to 'Unknown'."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        assert get_entity_name("category", {}) == "Unknown"
        assert get_entity_name("tag", {}) == "Unknown"


class TestFireflyClientExtensions:
    """Tests for FireflyClient tag and piggy bank methods."""

    def test_list_tags_parses_response(self, monkeypatch):
        """list_tags parses API response correctly."""
        from unittest.mock import MagicMock

        from paperless_firefly.firefly_client.client import FireflyClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": 1, "attributes": {"tag": "groceries", "description": "Food"}},
                {"id": 2, "attributes": {"tag": "rent", "description": None}},
            ],
            "meta": {"pagination": {"total_pages": 1}},
        }

        client = FireflyClient("http://test", "token")
        monkeypatch.setattr(client, "_request", lambda *a, **kw: mock_response)

        tags = client.list_tags()

        assert len(tags) == 2
        assert tags[0] == {"id": 1, "tag": "groceries", "description": "Food"}
        assert tags[1] == {"id": 2, "tag": "rent", "description": None}

    def test_list_piggy_banks_parses_response(self, monkeypatch):
        """list_piggy_banks parses API response correctly."""
        from unittest.mock import MagicMock

        from paperless_firefly.firefly_client.client import FireflyClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "id": 1,
                    "attributes": {
                        "name": "Vacation",
                        "target_amount": "1000.00",
                        "current_amount": "500.00",
                        "account_id": 5,
                        "notes": None,
                    },
                }
            ],
            "meta": {"pagination": {"total_pages": 1}},
        }

        client = FireflyClient("http://test", "token")
        monkeypatch.setattr(client, "_request", lambda *a, **kw: mock_response)

        piggy_banks = client.list_piggy_banks()

        assert len(piggy_banks) == 1
        assert piggy_banks[0]["name"] == "Vacation"
        assert piggy_banks[0]["target_amount"] == "1000.00"
        assert piggy_banks[0]["account_id"] == 5

    def test_create_tag(self, monkeypatch):
        """create_tag posts correct payload."""
        from unittest.mock import MagicMock

        from paperless_firefly.firefly_client.client import FireflyClient

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"id": 42}}

        client = FireflyClient("http://test", "token")
        
        captured_args = []
        def mock_request(*args, **kwargs):
            captured_args.append((args, kwargs))
            return mock_response
        
        monkeypatch.setattr(client, "_request", mock_request)

        tag_id = client.create_tag("groceries", "For food shopping")

        assert tag_id == 42
        assert len(captured_args) == 1
        assert captured_args[0][0] == ("POST", "/api/v1/tags")
        assert captured_args[0][1]["json_data"] == {"tag": "groceries", "description": "For food shopping"}

    def test_find_tag_by_name_found(self, monkeypatch):
        """find_tag_by_name returns matching tag."""
        from paperless_firefly.firefly_client.client import FireflyClient

        client = FireflyClient("http://test", "token")
        monkeypatch.setattr(
            client,
            "list_tags",
            lambda: [
                {"id": 1, "tag": "groceries", "description": None},
                {"id": 2, "tag": "rent", "description": None},
            ],
        )

        tag = client.find_tag_by_name("Groceries")  # Case-insensitive

        assert tag is not None
        assert tag["id"] == 1

    def test_find_tag_by_name_not_found(self, monkeypatch):
        """find_tag_by_name returns None if not found."""
        from paperless_firefly.firefly_client.client import FireflyClient

        client = FireflyClient("http://test", "token")
        monkeypatch.setattr(client, "list_tags", lambda: [])

        tag = client.find_tag_by_name("nonexistent")

        assert tag is None


class TestNewEntityFingerprints:
    """Tests for fingerprint computation of newly added entity types."""

    def test_budget_fingerprint_stable(self):
        """Budget fingerprint is stable and case-insensitive."""
        from paperless_firefly.services.sync_fingerprints import compute_budget_fingerprint

        data1 = {"name": "Monthly Groceries", "auto_budget_type": "rollover"}
        data2 = {"name": "monthly groceries", "auto_budget_type": "rollover"}

        assert compute_budget_fingerprint(data1) == compute_budget_fingerprint(data2)

    def test_budget_fingerprint_includes_auto_budget_type(self):
        """Budget fingerprint changes with auto_budget_type."""
        from paperless_firefly.services.sync_fingerprints import compute_budget_fingerprint

        data1 = {"name": "Groceries", "auto_budget_type": "rollover"}
        data2 = {"name": "Groceries", "auto_budget_type": "reset"}

        assert compute_budget_fingerprint(data1) != compute_budget_fingerprint(data2)

    def test_budget_fingerprint_requires_name(self):
        """Budget fingerprint raises if name is empty."""
        from paperless_firefly.services.sync_fingerprints import compute_budget_fingerprint

        with pytest.raises(ValueError, match="Budget must have a name"):
            compute_budget_fingerprint({"name": ""})

    def test_bill_fingerprint_stable(self):
        """Bill fingerprint is stable and case-insensitive."""
        from paperless_firefly.services.sync_fingerprints import compute_bill_fingerprint

        data1 = {"name": "Electric Bill", "amount_min": "50.00", "amount_max": "100.00", "repeat_freq": "monthly"}
        data2 = {"name": "electric bill", "amount_min": "50.00", "amount_max": "100.00", "repeat_freq": "monthly"}

        assert compute_bill_fingerprint(data1) == compute_bill_fingerprint(data2)

    def test_bill_fingerprint_includes_amounts(self):
        """Bill fingerprint changes with amount range."""
        from paperless_firefly.services.sync_fingerprints import compute_bill_fingerprint

        data1 = {"name": "Electric Bill", "amount_min": "50.00", "amount_max": "100.00"}
        data2 = {"name": "Electric Bill", "amount_min": "60.00", "amount_max": "120.00"}

        assert compute_bill_fingerprint(data1) != compute_bill_fingerprint(data2)

    def test_rule_group_fingerprint_uses_title(self):
        """Rule group fingerprint uses title field."""
        from paperless_firefly.services.sync_fingerprints import compute_rule_group_fingerprint

        data1 = {"title": "Auto Categorization"}
        data2 = {"title": "auto categorization"}

        assert compute_rule_group_fingerprint(data1) == compute_rule_group_fingerprint(data2)

    def test_rule_group_fingerprint_requires_title(self):
        """Rule group fingerprint raises if title is empty."""
        from paperless_firefly.services.sync_fingerprints import compute_rule_group_fingerprint

        with pytest.raises(ValueError, match="Rule group must have a title"):
            compute_rule_group_fingerprint({"title": ""})

    def test_currency_fingerprint_stable(self):
        """Currency fingerprint is based on code."""
        from paperless_firefly.services.sync_fingerprints import compute_currency_fingerprint

        data1 = {"code": "EUR", "name": "Euro", "symbol": "€"}
        data2 = {"code": "eur", "name": "EURO", "symbol": "€"}

        # Should be same (code is normalized)
        assert compute_currency_fingerprint(data1) == compute_currency_fingerprint(data2)

    def test_currency_fingerprint_requires_code(self):
        """Currency fingerprint raises if code is empty."""
        from paperless_firefly.services.sync_fingerprints import compute_currency_fingerprint

        with pytest.raises(ValueError, match="Currency must have a code"):
            compute_currency_fingerprint({"code": ""})

    def test_rule_fingerprint_uses_title(self):
        """Rule fingerprint is based on title."""
        from paperless_firefly.services.sync_fingerprints import compute_rule_fingerprint

        data1 = {"title": "Tag groceries", "rule_group_id": 1}
        data2 = {"title": "tag groceries", "rule_group_id": 1}

        assert compute_rule_fingerprint(data1) == compute_rule_fingerprint(data2)

    def test_recurrence_fingerprint_uses_title(self):
        """Recurrence fingerprint is based on title."""
        from paperless_firefly.services.sync_fingerprints import compute_recurrence_fingerprint

        data1 = {"title": "Monthly Rent", "repeat_freq": "monthly"}
        data2 = {"title": "monthly rent", "repeat_freq": "monthly"}

        assert compute_recurrence_fingerprint(data1) == compute_recurrence_fingerprint(data2)

    def test_transaction_fingerprint_uses_external_id(self):
        """Transaction fingerprint is based on external_id."""
        from paperless_firefly.services.sync_fingerprints import compute_transaction_fingerprint

        data1 = {"external_id": "TX-123", "description": "Coffee", "amount": "5.00"}
        data2 = {"external_id": "TX-123", "description": "Different", "amount": "10.00"}

        # Same external_id should produce same fingerprint
        assert compute_transaction_fingerprint(data1) == compute_transaction_fingerprint(data2)

    def test_transaction_fingerprint_without_external_id(self):
        """Transaction fingerprint falls back to hash fields."""
        from paperless_firefly.services.sync_fingerprints import compute_transaction_fingerprint

        data1 = {"description": "Coffee", "amount": "5.00", "date": "2024-01-15"}
        data2 = {"description": "Coffee", "amount": "5.00", "date": "2024-01-15"}

        # Should produce same fingerprint based on content
        assert compute_transaction_fingerprint(data1) == compute_transaction_fingerprint(data2)


class TestNewEntityFingerprints:
    """Tests for compute_fingerprint registry with new entity types."""

    def test_compute_fingerprint_all_new_types(self):
        """Generic compute_fingerprint works for all new entity types."""
        from paperless_firefly.services.sync_fingerprints import compute_fingerprint

        budget_data = {"name": "Test Budget"}
        bill_data = {"name": "Test Bill", "amount_min": "10.00", "amount_max": "20.00"}
        rule_group_data = {"title": "Test Rule Group"}
        currency_data = {"code": "EUR"}
        rule_data = {"title": "Test Rule"}
        recurrence_data = {"title": "Test Recurrence"}
        # Transaction requires date for fingerprint fallback
        transaction_data = {"external_id": "TX-123", "date": "2024-01-15", "amount": "10.00", "description": "Test"}

        # All should succeed and produce hex strings
        # Most produce 64-character hex strings (SHA256)
        assert len(compute_fingerprint("budget", budget_data)) == 64
        assert len(compute_fingerprint("bill", bill_data)) == 64
        assert len(compute_fingerprint("rule_group", rule_group_data)) == 64
        assert len(compute_fingerprint("currency", currency_data)) == 64
        assert len(compute_fingerprint("rule", rule_data)) == 64
        assert len(compute_fingerprint("recurrence", recurrence_data)) == 64
        # Transaction uses generate_external_id_v2 which produces 16-character hex
        tx_fp = compute_fingerprint("transaction", transaction_data)
        assert len(tx_fp) > 0
        assert all(c in "0123456789abcdef" for c in tx_fp)


class TestNormalizeEntityDataNewTypes:
    """Tests for entity data normalization of new types."""

    def test_normalize_budget(self):
        """Normalize budget extracts relevant fields."""
        from paperless_firefly.services.sync_fingerprints import normalize_entity_data

        raw = {
            "name": "Groceries",
            "auto_budget_type": "rollover",
            "auto_budget_amount": "500.00",
            "notes": "Monthly budget",
            "extra_field": "ignored",
        }
        normalized = normalize_entity_data("budget", raw)

        assert "name" in normalized
        assert "auto_budget_type" in normalized
        assert "extra_field" not in normalized

    def test_normalize_bill(self):
        """Normalize bill extracts relevant fields."""
        from paperless_firefly.services.sync_fingerprints import normalize_entity_data

        raw = {
            "name": "Electric",
            "amount_min": "50.00",
            "amount_max": "100.00",
            "date": "2024-01-15",
            "repeat_freq": "monthly",
            "extra_field": "ignored",
        }
        normalized = normalize_entity_data("bill", raw)

        assert "name" in normalized
        assert "amount_min" in normalized
        assert "repeat_freq" in normalized
        assert "extra_field" not in normalized

    def test_normalize_transaction(self):
        """Normalize transaction extracts relevant fields."""
        from paperless_firefly.services.sync_fingerprints import normalize_entity_data

        raw = {
            "external_id": "TX-123",
            "description": "Coffee",
            "amount": "5.00",
            "date": "2024-01-15",
            "type": "withdrawal",
            "source_name": "Wallet",
            "destination_name": "Starbucks",
            "extra_field": "ignored",
        }
        normalized = normalize_entity_data("transaction", raw)

        assert "external_id" in normalized
        assert "description" in normalized
        assert "source_name" in normalized
        assert "extra_field" not in normalized


class TestGetEntityNameNewTypes:
    """Tests for get_entity_name with new types."""

    def test_get_name_budget(self):
        """Get name for budget."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        data = {"name": "Monthly Groceries", "auto_budget_type": "rollover"}
        assert get_entity_name("budget", data) == "Monthly Groceries"

    def test_get_name_bill(self):
        """Get name for bill."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        data = {"name": "Electric Bill", "amount_min": "50.00"}
        assert get_entity_name("bill", data) == "Electric Bill"

    def test_get_name_rule_group(self):
        """Get name for rule group (uses title)."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        data = {"title": "Auto Categorization", "description": "Rules for auto-tagging"}
        assert get_entity_name("rule_group", data) == "Auto Categorization"

    def test_get_name_rule(self):
        """Get name for rule (uses title)."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        data = {"title": "Tag groceries", "rule_group_id": 1}
        assert get_entity_name("rule", data) == "Tag groceries"

    def test_get_name_recurrence(self):
        """Get name for recurrence (uses title)."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        data = {"title": "Monthly Rent", "repeat_freq": "monthly"}
        assert get_entity_name("recurrence", data) == "Monthly Rent"

    def test_get_name_currency(self):
        """Get name for currency (uses code - name format)."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        data = {"code": "EUR", "name": "Euro"}
        name = get_entity_name("currency", data)
        assert "EUR" in name  # Code should be included

    def test_get_name_transaction(self):
        """Get name for transaction (includes date and description)."""
        from paperless_firefly.services.sync_fingerprints import get_entity_name

        data = {"description": "Coffee at Starbucks", "amount": "5.00", "date": "2024-01-15"}
        name = get_entity_name("transaction", data)
        assert "Coffee at Starbucks" in name
        assert "5.00" in name
