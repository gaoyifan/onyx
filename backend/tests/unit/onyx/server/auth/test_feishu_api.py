from unittest.mock import AsyncMock

import httpx
import pytest

from onyx.error_handling.exceptions import OnyxError
from onyx.server.auth.feishu_api import build_synthetic_feishu_email
from onyx.server.auth.feishu_api import enrich_feishu_profile_with_user_id
from onyx.server.auth.feishu_api import exchange_feishu_code_for_user_token
from onyx.server.auth.feishu_api import extract_feishu_display_name
from onyx.server.auth.feishu_api import get_feishu_contact_user
from onyx.server.auth.feishu_api import get_required_feishu_user_id
from onyx.server.auth.feishu_api import select_feishu_account_id
from onyx.server.auth.feishu_api import sync_feishu_personal_name


def _httpx_json_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "https://open.feishu.cn/test"),
    )


def _httpx_get_json_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", "https://open.feishu.cn/test"),
    )


def test_build_synthetic_feishu_email_uses_normalized_user_id() -> None:
    assert build_synthetic_feishu_email("ou_xxx") == "ou_xxx@zhong-mo.com"
    assert (
        build_synthetic_feishu_email(" OU.AbC-123_456 ")
        == "ou.abc-123_456@zhong-mo.com"
    )


def test_build_synthetic_feishu_email_rejects_missing_user_id() -> None:
    with pytest.raises(OnyxError):
        build_synthetic_feishu_email(" ")


def test_get_required_feishu_user_id_falls_back_to_open_id() -> None:
    assert get_required_feishu_user_id(
        {"union_id": "on_union", "open_id": "ou_open"}
    ) == ("ou_open")


def test_get_required_feishu_user_id_rejects_missing_user_identity() -> None:
    with pytest.raises(OnyxError):
        get_required_feishu_user_id({"union_id": "on_union"})


def test_select_feishu_account_id_prefers_union_id() -> None:
    assert (
        select_feishu_account_id(
            {"union_id": "on_union", "user_id": "ou_user", "open_id": "ou_open"}
        )
        == "on_union"
    )


def test_select_feishu_account_id_falls_back_to_user_id() -> None:
    assert select_feishu_account_id({"user_id": "ou_user", "open_id": "ou_open"}) == (
        "ou_user"
    )


def test_select_feishu_account_id_rejects_missing_ids() -> None:
    with pytest.raises(OnyxError):
        select_feishu_account_id({})


@pytest.mark.asyncio
async def test_exchange_feishu_code_requires_user_access_token() -> None:
    response = _httpx_json_response({"code": 0, "data": {}})
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)

    with pytest.raises(OnyxError):
        await exchange_feishu_code_for_user_token(client, "code", "app-token")


@pytest.mark.asyncio
async def test_exchange_feishu_code_rejects_feishu_error() -> None:
    response = _httpx_json_response({"code": 999, "msg": "bad code"})
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)

    with pytest.raises(OnyxError):
        await exchange_feishu_code_for_user_token(client, "code", "app-token")


@pytest.mark.asyncio
async def test_get_feishu_contact_user_extracts_user() -> None:
    response = _httpx_get_json_response(
        {"code": 0, "data": {"user": {"user_id": "ou_user"}}}
    )
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)

    user = await get_feishu_contact_user(
        client,
        "tenant-token",
        "ou_open",
        "open_id",
    )

    assert user["user_id"] == "ou_user"


@pytest.mark.asyncio
async def test_enrich_feishu_profile_resolves_user_id_by_open_id(monkeypatch) -> None:
    async def mock_tenant_token(_client: object) -> str:
        return "tenant-token"

    async def mock_contact_user(
        _client: object,
        tenant_access_token: str,
        user_identifier: str,
        user_id_type: str,
    ) -> dict[str, str]:
        assert tenant_access_token == "tenant-token"
        assert user_identifier == "ou_open"
        assert user_id_type == "open_id"
        return {"user_id": "ou_user", "open_id": "ou_open"}

    monkeypatch.setattr(
        "onyx.server.auth.feishu_api.get_feishu_tenant_access_token",
        mock_tenant_token,
    )
    monkeypatch.setattr(
        "onyx.server.auth.feishu_api.get_feishu_contact_user",
        mock_contact_user,
    )

    profile = await enrich_feishu_profile_with_user_id(
        AsyncMock(),
        {"union_id": "on_union", "open_id": "ou_open"},
    )

    assert profile["user_id"] == "ou_user"
    assert profile["union_id"] == "on_union"


def test_extract_feishu_display_name_prefers_localized_name() -> None:
    assert (
        extract_feishu_display_name({"name": "李登登", "en_name": "Dengdeng Li"})
        == "李登登"
    )


def test_extract_feishu_display_name_falls_back_to_en_name() -> None:
    assert (
        extract_feishu_display_name({"name": "  ", "en_name": "Dengdeng Li"})
        == "Dengdeng Li"
    )


def test_extract_feishu_display_name_strips_surrounding_whitespace() -> None:
    assert extract_feishu_display_name({"name": "  李登登  "}) == "李登登"


def test_extract_feishu_display_name_returns_none_when_missing() -> None:
    assert extract_feishu_display_name({}) is None
    assert extract_feishu_display_name({"name": "", "en_name": "   "}) is None
    assert extract_feishu_display_name({"name": 42}) is None


def _make_user_manager_mock() -> AsyncMock:
    manager = AsyncMock()
    manager.user_db = AsyncMock()
    manager.user_db.update = AsyncMock()
    return manager


class _FakeUser:
    def __init__(
        self, personal_name: str | None = None, email: str = "ou_open@zhong-mo.com"
    ) -> None:
        self.personal_name = personal_name
        self.email = email


@pytest.mark.asyncio
async def test_sync_feishu_personal_name_sets_when_empty() -> None:
    manager = _make_user_manager_mock()
    user = _FakeUser(personal_name=None)

    await sync_feishu_personal_name(manager, user, {"name": "李登登"})

    manager.user_db.update.assert_awaited_once_with(user, {"personal_name": "李登登"})
    assert user.personal_name == "李登登"


@pytest.mark.asyncio
async def test_sync_feishu_personal_name_skips_when_already_set() -> None:
    manager = _make_user_manager_mock()
    user = _FakeUser(personal_name="Manual Override")

    await sync_feishu_personal_name(manager, user, {"name": "李登登"})

    manager.user_db.update.assert_not_awaited()
    assert user.personal_name == "Manual Override"


@pytest.mark.asyncio
async def test_sync_feishu_personal_name_skips_when_profile_lacks_name() -> None:
    manager = _make_user_manager_mock()
    user = _FakeUser(personal_name=None)

    await sync_feishu_personal_name(manager, user, {"open_id": "ou_open"})

    manager.user_db.update.assert_not_awaited()
    assert user.personal_name is None


@pytest.mark.asyncio
async def test_sync_feishu_personal_name_swallows_db_errors() -> None:
    manager = _make_user_manager_mock()
    manager.user_db.update.side_effect = RuntimeError("db down")
    user = _FakeUser(personal_name=None)

    await sync_feishu_personal_name(manager, user, {"name": "李登登"})

    assert user.personal_name is None
