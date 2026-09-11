from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from app.integrations.vpn.config import InboundConfig, VlessConnection, remote_expiry_ms
from app.integrations.vpn.errors import (
    AlreadyExists,
    AmbiguousOperation,
    AuthenticationError,
    NotFound,
    PermissionError,
    Timeout,
    Unavailable,
    ValidationError,
    transport_error,
)
from app.integrations.vpn.threexui import ThreeXUIConfig

CLIENT_ID = UUID("3a9a3bb0-bfff-41fa-a3ec-53fc522ad31b")


@pytest.mark.parametrize(
    "inbound",
    [
        dict(network="tcp", security="none"),
        dict(
            network="tcp",
            security="reality",
            sni="example.org",
            reality_public_key="observed-key",
            short_id="",
            fingerprint="chrome",
            flow="xtls-rprx-vision",
        ),
        dict(
            network="ws",
            security="tls",
            sni="example.org",
            path="/тест?a=b&c=d",
            transport_host="cdn.example.org",
        ),
        dict(network="grpc", security="tls", service_name="vpn service"),
    ],
)
def test_uri_roundtrip(inbound):
    connection = VlessConnection(
        uuid=CLIENT_ID,
        host="2001:db8::1",
        port=8443,
        inbound=InboundConfig(**inbound),
        label="Мой VPN #1",
    )
    assert VlessConnection.parse(connection.uri()) == connection
    if inbound["security"] == "none":
        assert "sni=" not in connection.uri() and "pbk=" not in connection.uri()


@pytest.mark.parametrize("suffix", ["&security=tls", "&unknown=value"])
def test_uri_reject_ambiguous_fields(suffix):
    uri = f"vless://{CLIENT_ID}@example.org:443?encryption=none&type=tcp&security=none{suffix}"
    with pytest.raises(ValueError):
        VlessConnection.parse(uri)


def test_uri_requires_observed_fields():
    with pytest.raises(ValueError):
        InboundConfig(network="tcp", security="reality")
    with pytest.raises(ValueError):
        InboundConfig(security="tls")


def test_remote_expiry_is_paid_window_only():
    start = datetime(2026, 3, 28, 12, tzinfo=UTC)
    end = start + timedelta(days=1, microseconds=999)
    assert remote_expiry_ms(end, start) == 1774785600000
    with pytest.raises(ValueError):
        remote_expiry_ms(start, end)
    with pytest.raises(ValueError):
        remote_expiry_ms(end.replace(tzinfo=None), start)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthenticationError),
        (403, PermissionError),
        (404, NotFound),
        (409, AlreadyExists),
        (422, ValidationError),
        (503, AmbiguousOperation),
    ],
)
def test_provider_http_error_mapping(status, expected):
    request = httpx.Request("POST", "https://panel.example")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError("never expose raw body", request=request, response=response)
    mapped = transport_error(error, mutation=True)
    assert isinstance(mapped, expected)
    assert "never expose" not in str(mapped)


def test_provider_transport_ambiguity():
    assert isinstance(
        transport_error(httpx.ReadTimeout("secret"), mutation=True), AmbiguousOperation
    )
    assert isinstance(transport_error(httpx.ReadTimeout("secret"), mutation=False), Timeout)
    assert isinstance(transport_error(httpx.ConnectError("secret"), mutation=False), Unavailable)


def test_panel_config_is_complete_and_https():
    assert ThreeXUIConfig() == ThreeXUIConfig()
    with pytest.raises(ValueError):
        ThreeXUIConfig(base_url="https://panel.example")
    with pytest.raises(ValueError):
        ThreeXUIConfig(
            base_url="http://panel.example",
            version="2.8.4",
            username="operator",
            password="secret",
        )
    config = ThreeXUIConfig(
        base_url="https://panel.example/base",
        version="observed-version",
        username="operator",
        password="secret",
    )
    assert config.base_url.endswith("/base")
