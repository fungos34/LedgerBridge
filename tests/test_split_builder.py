"""
Tests for the split builder module.

Tests cover:
- Amount validation (SSOT for positive amount enforcement)
- Split transaction building
- Rounding strategies
- Privacy enforcement via interpretation trace
"""

from decimal import Decimal

import pytest

from paperless_firefly.schemas.split_builder import (
    CURRENCY_PRECISION,
    AmountValidationError,
    RoundingStrategy,
    SplitItem,
    SplitTransactionPayload,
    SplitValidationError,
    build_split_transaction_payload,
    build_splits_from_line_items,
    normalize_amount_for_firefly,
    validate_amount,
)


class TestValidateAmount:
    """Tests for the validate_amount function (SSOT for amount validation)."""

    def test_valid_positive_decimal(self) -> None:
        """Test positive Decimal amount is accepted."""
        result = validate_amount(Decimal("10.50"))
        assert result == Decimal("10.50")

    def test_valid_positive_string(self) -> None:
        """Test positive string amount is converted to Decimal."""
        result = validate_amount("25.99")
        assert result == Decimal("25.99")

    def test_valid_positive_float(self) -> None:
        """Test positive float amount is converted to Decimal."""
        result = validate_amount(50.25)
        # Float conversion may have precision issues, but should work
        assert result == Decimal("50.25")

    def test_negative_amount_raises_error(self) -> None:
        """Test negative amount raises AmountValidationError."""
        with pytest.raises(AmountValidationError) as exc_info:
            validate_amount(Decimal("-10.00"))
        assert "must be positive" in str(exc_info.value)
        assert "withdrawal/deposit" in str(exc_info.value)  # Helpful hint

    def test_negative_string_amount_raises_error(self) -> None:
        """Test negative string amount raises AmountValidationError."""
        with pytest.raises(AmountValidationError) as exc_info:
            validate_amount("-5.50")
        assert "must be positive" in str(exc_info.value)

    def test_zero_amount_raises_by_default(self) -> None:
        """Test zero amount raises error when allow_zero=False (default)."""
        with pytest.raises(AmountValidationError) as exc_info:
            validate_amount(Decimal("0.00"))
        assert "cannot be zero" in str(exc_info.value)

    def test_zero_amount_allowed_when_specified(self) -> None:
        """Test zero amount is accepted when allow_zero=True."""
        result = validate_amount(Decimal("0.00"), allow_zero=True)
        assert result == Decimal("0.00")

    def test_amount_quantized_to_precision(self) -> None:
        """Test amount is quantized to 2 decimal places."""
        result = validate_amount(Decimal("10.555"))
        assert result == Decimal("10.56")  # ROUND_HALF_UP

    def test_max_amount_enforced(self) -> None:
        """Test maximum amount validation."""
        with pytest.raises(AmountValidationError) as exc_info:
            validate_amount(Decimal("1000000"), max_amount=Decimal("100000"))
        assert "exceeds maximum" in str(exc_info.value)

    def test_max_amount_passes_when_under(self) -> None:
        """Test amount under max_amount passes."""
        result = validate_amount(Decimal("500"), max_amount=Decimal("1000"))
        assert result == Decimal("500.00")

    def test_invalid_string_raises_error(self) -> None:
        """Test invalid string raises AmountValidationError."""
        with pytest.raises(AmountValidationError) as exc_info:
            validate_amount("not-a-number")
        assert "Invalid amount format" in str(exc_info.value)

    def test_custom_field_name_in_error(self) -> None:
        """Test custom field_name appears in error message."""
        with pytest.raises(AmountValidationError) as exc_info:
            validate_amount("-5.00", field_name="split_total")
        assert "split_total" in str(exc_info.value)


class TestNormalizeAmountForFirefly:
    """Tests for normalize_amount_for_firefly function."""

    def test_positive_amount_unchanged(self) -> None:
        """Test positive amount returns string."""
        result = normalize_amount_for_firefly(Decimal("50.00"))
        assert result == "50.00"

    def test_negative_amount_becomes_positive(self) -> None:
        """Test negative amount is converted to positive."""
        result = normalize_amount_for_firefly(Decimal("-25.50"))
        assert result == "25.50"

    def test_string_negative_normalized(self) -> None:
        """Test negative string amount is normalized."""
        result = normalize_amount_for_firefly("-100.00")
        assert result == "100.00"

    def test_float_handled(self) -> None:
        """Test float input is handled."""
        result = normalize_amount_for_firefly(-75.25)
        assert result == "75.25"

    def test_precision_applied(self) -> None:
        """Test result is quantized to 2 decimal places."""
        result = normalize_amount_for_firefly(Decimal("10.999"))
        assert result == "11.00"


class TestSplitItem:
    """Tests for SplitItem dataclass."""

    def test_stable_key_deterministic(self) -> None:
        """Test stable_key produces consistent output."""
        item = SplitItem(
            amount=Decimal("50.00"),
            description="Groceries",
            category="Food",
            order=0,
            _position_in_source=1,
        )
        key1 = item.stable_key()
        key2 = item.stable_key()
        assert key1 == key2

    def test_stable_key_uses_position(self) -> None:
        """Test stable_key uses position_in_source if available."""
        item = SplitItem(
            amount=Decimal("50.00"),
            description="Test",
            category=None,
            order=5,
            _position_in_source=1,
        )
        key = item.stable_key()
        assert key.startswith("1:")  # Uses position_in_source, not order


class TestSplitTransactionPayload:
    """Tests for SplitTransactionPayload validation."""

    def test_validation_passes_for_valid_payload(self) -> None:
        """Test validation returns empty list for valid payload."""
        payload = SplitTransactionPayload(
            transaction_type="withdrawal",
            date="2024-01-15",
            source_name="Checking",
            destination_name="Supermarket",
            currency_code="EUR",
            group_title="Shopping Trip",
            tags=["paperless"],
            splits=[
                SplitItem(Decimal("30.00"), "Food", "Groceries", 0),
                SplitItem(Decimal("20.00"), "Household", "Supplies", 1),
            ],
            external_id="PAPERLESS:123:abc",
            internal_reference="PAPERLESS:123",
            notes="Test notes",
            external_url=None,
            total_amount=Decimal("50.00"),
        )
        errors = payload.validate()
        assert errors == []

    def test_validation_fails_for_sum_mismatch(self) -> None:
        """Test validation catches sum != total."""
        payload = SplitTransactionPayload(
            transaction_type="withdrawal",
            date="2024-01-15",
            source_name="Checking",
            destination_name="Supermarket",
            currency_code="EUR",
            group_title="Shopping Trip",
            tags=[],
            splits=[
                SplitItem(Decimal("30.00"), "Food", "Groceries", 0),
                SplitItem(Decimal("15.00"), "Household", "Supplies", 1),  # Should be 20
            ],
            external_id="PAPERLESS:123:abc",
            internal_reference="PAPERLESS:123",
            notes="Test notes",
            external_url=None,
            total_amount=Decimal("50.00"),
        )
        errors = payload.validate()
        assert len(errors) > 0
        assert any("sum" in e.lower() for e in errors)

    def test_validation_fails_for_empty_description(self) -> None:
        """Test validation catches empty split descriptions."""
        payload = SplitTransactionPayload(
            transaction_type="withdrawal",
            date="2024-01-15",
            source_name="Checking",
            destination_name="Supermarket",
            currency_code="EUR",
            group_title="Test",
            tags=[],
            splits=[
                SplitItem(Decimal("50.00"), "", None, 0),  # Empty description
            ],
            external_id="PAPERLESS:123:abc",
            internal_reference="PAPERLESS:123",
            notes="Test notes",
            external_url=None,
            total_amount=Decimal("50.00"),
        )
        errors = payload.validate()
        assert len(errors) > 0
        assert any("description" in e.lower() for e in errors)

    def test_validation_fails_for_non_positive_amount(self) -> None:
        """Test validation catches non-positive split amounts."""
        payload = SplitTransactionPayload(
            transaction_type="withdrawal",
            date="2024-01-15",
            source_name="Checking",
            destination_name="Supermarket",
            currency_code="EUR",
            group_title="Test",
            tags=[],
            splits=[
                SplitItem(Decimal("0.00"), "Item", None, 0),  # Zero amount
            ],
            external_id="PAPERLESS:123:abc",
            internal_reference="PAPERLESS:123",
            notes="Test notes",
            external_url=None,
            total_amount=Decimal("0.00"),
        )
        errors = payload.validate()
        assert len(errors) > 0
        assert any("non-positive" in e.lower() for e in errors)


class TestRoundingStrategy:
    """Tests for rounding strategy constants."""

    def test_distribute_remainder_exists(self) -> None:
        """Test DISTRIBUTE_REMAINDER strategy exists."""
        assert RoundingStrategy.DISTRIBUTE_REMAINDER.value == "distribute_remainder"

    def test_proportional_exists(self) -> None:
        """Test PROPORTIONAL strategy exists."""
        assert RoundingStrategy.PROPORTIONAL.value == "proportional"


class TestCurrencyPrecision:
    """Tests for currency precision constant."""

    def test_precision_is_two_decimals(self) -> None:
        """Test CURRENCY_PRECISION is 0.01."""
        assert CURRENCY_PRECISION == Decimal("0.01")
