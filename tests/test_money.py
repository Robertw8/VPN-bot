import pytest

from app.bot.security import require_admin
from app.config import Settings
from app.domain.errors import DomainError, Forbidden
from app.domain.money import amount, percentage, rub


def test_money():
    assert amount("6,66") == 666
    assert rub(666) == "6.66 ₽"
    assert percentage(45000, 2000) == 9000


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "1.001", "1000001", "abc"])
def test_bad_money(value):
    with pytest.raises(DomainError):
        amount(value)


def test_admin():
    config = Settings(_env_file=None, admin_telegram_ids=[123])
    require_admin(config, 123)
    with pytest.raises(Forbidden):
        require_admin(config, 456)
