import pytest

from app.config import Settings
from app.domain.errors import DomainError
from app.logging import error_details
from app.startup import validate_config


@pytest.mark.parametrize("token", ["", "not-a-token"])
def test_startup_rejects_missing_invalid_token(token):
    with pytest.raises(DomainError) as caught:
        validate_config(Settings(_env_file=None, bot_token=token))
    assert "BOT_TOKEN" in str(caught.value)
    assert "not-a-token" not in str(caught.value)


def test_error_details_do_not_include_secret_exception_text():
    try:
        raise RuntimeError("sensitive-vless-secret")
    except RuntimeError as exc:
        details = error_details(exc)
    assert details["error_type"] == "RuntimeError"
    assert details["origin"]
    assert "sensitive-vless-secret" not in str(details)


def test_startup_rejects_invalid_encryption_key():
    with pytest.raises(DomainError):
        validate_config(Settings(_env_file=None, encryption_key="invalid"), require_token=False)
