import re
from decimal import Decimal

from app.domain.errors import DomainError

MAX_AMOUNT = 100_000_000


def amount(value: str) -> int:
    """Strict decimal rubles: no exponents, binary floats or Decimal rounding."""
    value = value.strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", value):
        raise DomainError("Введите сумму в рублях, не более двух знаков после запятой.")
    whole, _, fraction = value.partition(".")
    cents = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if not 0 < cents <= MAX_AMOUNT:
        raise DomainError("Сумма должна быть больше нуля и не превышать 1 000 000 ₽.")
    return cents


def rub(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f} ₽"


def percent_text(basis_points: int) -> str:
    return f"{Decimal(basis_points) / 100:g}%"


def percentage(cents: int, basis_points: int) -> int:
    if (
        type(cents) is not int
        or type(basis_points) is not int
        or cents < 0
        or not 0 <= basis_points <= 10000
    ):
        raise ValueError("Invalid integer money/percentage")
    return cents * basis_points // 10_000
