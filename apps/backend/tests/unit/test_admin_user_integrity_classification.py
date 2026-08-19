"""
Which integrity failure is a taken address, and which is not (N4).

The first version of this path assumed everything that was not the team
foreign key was a duplicate address. That assumption is invisible while the
users table has exactly two constraints, and it becomes a lie the day somebody
adds a third: the operator is told their file has a duplicate email on a row
whose address is fine, and the bulk report says ``email_taken`` for it.

Tested here rather than through the API because the case worth pinning cannot
be produced through the API today. A test that can only run once the defect
exists is a test written after the incident.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from services.admin_user_service import _is_duplicate_email


class _Diag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


class _Orig(Exception):
    def __init__(self, constraint_name: str | None, message: str = "") -> None:
        super().__init__(message)
        self.diag = _Diag(constraint_name)
        self._message = message

    def __str__(self) -> str:
        return self._message


def _violation(constraint_name: str | None, message: str = "") -> IntegrityError:
    return IntegrityError("INSERT ...", {}, _Orig(constraint_name, message))


def test_the_unique_index_on_the_address_is_a_taken_address() -> None:
    assert _is_duplicate_email(_violation("uq_users_email")) is True


def test_another_constraint_on_the_same_table_is_not() -> None:
    """The case the first version got wrong.

    Reporting this as a duplicate address tells the operator something false
    about their own data, and there is nothing in the response to correct it.
    """
    assert _is_duplicate_email(_violation("ck_users_service_account_password")) is False


def test_a_driver_that_names_no_constraint_falls_back_to_the_message() -> None:
    """Not every driver fills in the diagnostics, and the message is what is
    left. Matching the index name in it is narrower than matching nothing."""
    assert _is_duplicate_email(
        _violation(None, 'duplicate key value violates unique constraint "users_email_key"')
    ) is True


def test_an_unnamed_violation_with_an_unrelated_message_is_not_an_address() -> None:
    assert _is_duplicate_email(_violation(None, "null value in column jti")) is False
