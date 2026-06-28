from uuid import UUID

import pytest

from alphalab.core import DomainValidationError, new_order_id, new_uuid, validate_uuid_id


def test_new_uuid_returns_valid_uuid4_string() -> None:
    value = new_uuid()

    parsed = UUID(value)

    assert str(parsed) == value
    assert parsed.version == 4


def test_typed_id_helpers_return_uuid_strings() -> None:
    order_id = new_order_id()

    validate_uuid_id(order_id, "order_id")
    assert isinstance(order_id, str)


def test_validate_uuid_id_rejects_invalid_values() -> None:
    with pytest.raises(DomainValidationError):
        validate_uuid_id("not-a-uuid", "order_id")
