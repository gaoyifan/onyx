import re
import secrets
from typing import Any

import httpx
import jwt
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi import Response
from fastapi_users import exceptions
from fastapi_users.authentication import Strategy
from fastapi_users.jwt import decode_jwt
from fastapi_users.jwt import generate_jwt
from pydantic import BaseModel
from starlette.responses import JSONResponse

from onyx.auth.users import auth_backend
from onyx.auth.users import get_user_manager
from onyx.auth.users import UserManager
from onyx.configs.app_configs import FEISHU_APP_ID
from onyx.configs.app_configs import FEISHU_APP_SECRET
from onyx.configs.app_configs import FEISHU_AUTH_ENABLED
from onyx.configs.app_configs import FEISHU_H5_SDK_URL
from onyx.configs.app_configs import FEISHU_SYNTHETIC_EMAIL_DOMAIN
from onyx.configs.app_configs import USER_AUTH_SECRET
from onyx.configs.app_configs import WEB_DOMAIN
from onyx.configs.constants import PUBLIC_API_TAGS
from onyx.db.models import User
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.utils.logger import setup_logger

logger = setup_logger()

router = APIRouter(prefix="/auth/feishu", tags=PUBLIC_API_TAGS)

FEISHU_STATE_AUDIENCE = "onyx:feishu-state"
FEISHU_CSRF_TOKEN_KEY = "csrf"
FEISHU_NEXT_URL_KEY = "next_url"
FEISHU_STATE_COOKIE_NAME = "onyx_feishu_auth_csrf"
FEISHU_STATE_LIFETIME_SECONDS = 10 * 60
FEISHU_OAUTH_NAME = "feishu"

FEISHU_APP_ACCESS_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
)
FEISHU_TENANT_ACCESS_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
FEISHU_USER_ACCESS_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/access_token"
FEISHU_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
FEISHU_CONTACT_USER_URL = "https://open.feishu.cn/open-apis/contact/v3/users"

SAFE_LOCAL_PART_PATTERN = re.compile(r"[^a-z0-9._-]+")


class FeishuConfigResponse(BaseModel):
    enabled: bool
    app_id: str
    state: str
    sdk_url: str


class FeishuLoginRequest(BaseModel):
    code: str
    state: str


class FeishuLoginResponse(BaseModel):
    redirect_url: str


def feishu_auth_configured() -> bool:
    return FEISHU_AUTH_ENABLED and bool(FEISHU_APP_ID and FEISHU_APP_SECRET)


def _safe_next_url(next_url: str | None) -> str:
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/app"
    return next_url


def build_feishu_state(next_url: str | None) -> tuple[str, str]:
    if not USER_AUTH_SECRET:
        raise OnyxError(
            OnyxErrorCode.INTERNAL_ERROR,
            "USER_AUTH_SECRET is required for Feishu authentication.",
        )

    csrf_token = secrets.token_urlsafe(32)
    state = generate_jwt(
        {
            "aud": FEISHU_STATE_AUDIENCE,
            FEISHU_CSRF_TOKEN_KEY: csrf_token,
            FEISHU_NEXT_URL_KEY: _safe_next_url(next_url),
        },
        USER_AUTH_SECRET,
        FEISHU_STATE_LIFETIME_SECONDS,
    )
    return state, csrf_token


def decode_and_validate_feishu_state(state: str, cookie_csrf_token: str | None) -> str:
    if not USER_AUTH_SECRET:
        raise OnyxError(
            OnyxErrorCode.INTERNAL_ERROR,
            "USER_AUTH_SECRET is required for Feishu authentication.",
        )

    try:
        state_data = decode_jwt(state, USER_AUTH_SECRET, [FEISHU_STATE_AUDIENCE])
    except jwt.ExpiredSignatureError:
        raise OnyxError(OnyxErrorCode.UNAUTHORIZED, "Feishu login state expired.")
    except jwt.PyJWTError:
        raise OnyxError(OnyxErrorCode.UNAUTHORIZED, "Invalid Feishu login state.")

    state_csrf_token = state_data.get(FEISHU_CSRF_TOKEN_KEY)
    if (
        not cookie_csrf_token
        or not state_csrf_token
        or not secrets.compare_digest(cookie_csrf_token, state_csrf_token)
    ):
        raise OnyxError(OnyxErrorCode.UNAUTHORIZED, "Invalid Feishu login state.")

    return _safe_next_url(state_data.get(FEISHU_NEXT_URL_KEY))


def normalize_feishu_user_id(user_id: str) -> str:
    normalized = SAFE_LOCAL_PART_PATTERN.sub("-", user_id.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip(".-_")
    if not normalized:
        raise OnyxError(OnyxErrorCode.VALIDATION_ERROR, "Missing Feishu user id.")
    return normalized


def build_synthetic_feishu_email(
    user_id: str, domain: str = FEISHU_SYNTHETIC_EMAIL_DOMAIN
) -> str:
    normalized_domain = domain.strip().lower()
    if not normalized_domain or "@" in normalized_domain:
        raise OnyxError(
            OnyxErrorCode.INTERNAL_ERROR,
            "FEISHU_SYNTHETIC_EMAIL_DOMAIN must be an email domain.",
        )
    return f"{normalize_feishu_user_id(user_id)}@{normalized_domain}"


def select_feishu_account_id(profile: dict[str, Any]) -> str:
    account_id = (
        profile.get("union_id") or profile.get("user_id") or profile.get("open_id")
    )
    if not isinstance(account_id, str) or not account_id.strip():
        raise OnyxError(OnyxErrorCode.VALIDATION_ERROR, "Missing Feishu account id.")
    return account_id


def get_required_feishu_user_id(profile: dict[str, Any]) -> str:
    # Some Feishu H5 auth responses omit the enterprise-scoped `user_id` unless
    # additional contact permissions are granted, but still include the app user
    # identifier as `open_id` (typically also an `ou_...` value). Use it only as
    # a fallback for the synthetic Onyx email local part.
    user_id = profile.get("user_id") or profile.get("open_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise OnyxError(OnyxErrorCode.VALIDATION_ERROR, "Missing Feishu user id.")
    return user_id


def extract_feishu_display_name(profile: dict[str, Any]) -> str | None:
    """Return the human-readable Feishu display name from a profile payload.

    Prefers the localized ``name`` (typically Chinese) and falls back to
    ``en_name`` so we never store an opaque ``ou_...`` identifier when a real
    name is available. Returns ``None`` when neither field is set, which is
    treated by the caller as "do not update".
    """
    for key in ("name", "en_name"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def sync_feishu_personal_name(
    user_manager: UserManager,
    user: User,
    profile: dict[str, Any],
) -> None:
    """Backfill ``personal_name`` on the Onyx user from the Feishu profile.

    Only writes when the user has no existing ``personal_name`` so that a
    user's manual customization in the Onyx Personalization tab is never
    overwritten on subsequent Feishu logins.
    """
    display_name = extract_feishu_display_name(profile)
    if not display_name:
        return
    if (user.personal_name or "").strip():
        return

    try:
        await user_manager.user_db.update(user, {"personal_name": display_name})
    except Exception:
        logger.exception(
            "Failed to sync Feishu display name for user %s; continuing login",
            user.email,
        )
        return

    user.personal_name = display_name


def _extract_feishu_data(payload: dict[str, Any], *, action: str) -> dict[str, Any]:
    if payload.get("code") != 0:
        logger.warning("Feishu %s failed: %s", action, payload.get("msg"))
        raise OnyxError(
            OnyxErrorCode.UNAUTHORIZED,
            f"Feishu {action} failed.",
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise OnyxError(
            OnyxErrorCode.UNAUTHORIZED,
            f"Feishu {action} returned no data.",
        )
    return data


async def get_feishu_app_access_token(client: httpx.AsyncClient) -> str:
    response = await client.post(
        FEISHU_APP_ACCESS_TOKEN_URL,
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        logger.warning("Feishu app token request failed: %s", payload.get("msg"))
        raise OnyxError(OnyxErrorCode.UNAUTHORIZED, "Feishu app auth failed.")

    app_access_token = payload.get("app_access_token")
    if not isinstance(app_access_token, str) or not app_access_token:
        raise OnyxError(
            OnyxErrorCode.UNAUTHORIZED, "Feishu app auth returned no token."
        )
    return app_access_token


async def get_feishu_tenant_access_token(client: httpx.AsyncClient) -> str:
    response = await client.post(
        FEISHU_TENANT_ACCESS_TOKEN_URL,
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        logger.warning("Feishu tenant token request failed: %s", payload.get("msg"))
        raise OnyxError(OnyxErrorCode.UNAUTHORIZED, "Feishu tenant auth failed.")

    tenant_access_token = payload.get("tenant_access_token")
    if not isinstance(tenant_access_token, str) or not tenant_access_token:
        raise OnyxError(
            OnyxErrorCode.UNAUTHORIZED, "Feishu tenant auth returned no token."
        )
    return tenant_access_token


async def exchange_feishu_code_for_user_token(
    client: httpx.AsyncClient, code: str, app_access_token: str
) -> dict[str, Any]:
    response = await client.post(
        FEISHU_USER_ACCESS_TOKEN_URL,
        headers={"Authorization": f"Bearer {app_access_token}"},
        json={"grant_type": "authorization_code", "code": code},
    )
    response.raise_for_status()
    token_data = _extract_feishu_data(response.json(), action="user token exchange")
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OnyxError(
            OnyxErrorCode.UNAUTHORIZED,
            "Feishu user token exchange returned no access token.",
        )
    return token_data


async def get_feishu_user_info(
    client: httpx.AsyncClient, user_access_token: str
) -> dict[str, Any]:
    response = await client.get(
        FEISHU_USER_INFO_URL,
        headers={"Authorization": f"Bearer {user_access_token}"},
    )
    response.raise_for_status()
    return _extract_feishu_data(response.json(), action="user info lookup")


async def get_feishu_contact_user(
    client: httpx.AsyncClient,
    tenant_access_token: str,
    user_identifier: str,
    user_id_type: str,
) -> dict[str, Any]:
    response = await client.get(
        f"{FEISHU_CONTACT_USER_URL}/{user_identifier}",
        headers={"Authorization": f"Bearer {tenant_access_token}"},
        params={"user_id_type": user_id_type},
    )
    response.raise_for_status()
    data = _extract_feishu_data(response.json(), action="contact user lookup")
    user = data.get("user")
    if not isinstance(user, dict):
        raise OnyxError(
            OnyxErrorCode.UNAUTHORIZED,
            "Feishu contact user lookup returned no user.",
        )
    return user


async def enrich_feishu_profile_with_user_id(
    client: httpx.AsyncClient, profile: dict[str, Any]
) -> dict[str, Any]:
    if isinstance(profile.get("user_id"), str) and profile["user_id"].strip():
        return profile

    lookup_candidates = (
        ("open_id", profile.get("open_id")),
        ("union_id", profile.get("union_id")),
    )
    tenant_access_token: str | None = None
    for user_id_type, user_identifier in lookup_candidates:
        if not isinstance(user_identifier, str) or not user_identifier.strip():
            continue

        tenant_access_token = (
            tenant_access_token or await get_feishu_tenant_access_token(client)
        )
        try:
            contact_user = await get_feishu_contact_user(
                client,
                tenant_access_token,
                user_identifier,
                user_id_type,
            )
        except (OnyxError, httpx.HTTPError):
            logger.debug(
                "Feishu user_id lookup by %s failed; trying next identifier",
                user_id_type,
            )
            continue

        if (
            isinstance(contact_user.get("user_id"), str)
            and contact_user["user_id"].strip()
        ):
            return {**profile, **contact_user}

    logger.debug(
        "Feishu profile did not include user_id; available fields: %s",
        sorted(profile.keys()),
    )
    return profile


async def get_feishu_user_profile(code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            app_access_token = await get_feishu_app_access_token(client)
            token_data = await exchange_feishu_code_for_user_token(
                client, code, app_access_token
            )
            user_info = await get_feishu_user_info(client, token_data["access_token"])
            profile = await enrich_feishu_profile_with_user_id(
                client, {**token_data, **user_info}
            )
            return token_data, profile
    except httpx.HTTPError:
        logger.exception("Feishu auth HTTP request failed")
        raise OnyxError(OnyxErrorCode.UNAUTHORIZED, "Feishu auth request failed.")


@router.get("/config")
async def get_feishu_config(
    response: Response, next: str | None = None
) -> FeishuConfigResponse:
    enabled = feishu_auth_configured()
    state = ""

    if enabled:
        state, csrf_token = build_feishu_state(next)
        response.set_cookie(
            key=FEISHU_STATE_COOKIE_NAME,
            value=csrf_token,
            max_age=FEISHU_STATE_LIFETIME_SECONDS,
            path="/",
            secure=WEB_DOMAIN.startswith("https"),
            httponly=True,
            samesite="lax",
        )

    return FeishuConfigResponse(
        enabled=enabled,
        app_id=FEISHU_APP_ID if enabled else "",
        state=state,
        sdk_url=FEISHU_H5_SDK_URL,
    )


@router.post("/login")
async def feishu_login(
    body: FeishuLoginRequest,
    request: Request,
    user_manager: UserManager = Depends(get_user_manager),
    strategy: Strategy[Any, Any] = Depends(auth_backend.get_strategy),
) -> JSONResponse:
    if not feishu_auth_configured():
        raise OnyxError(
            OnyxErrorCode.UNAUTHORIZED, "Feishu authentication is disabled."
        )

    next_url = decode_and_validate_feishu_state(
        body.state, request.cookies.get(FEISHU_STATE_COOKIE_NAME)
    )

    token_data, profile = await get_feishu_user_profile(body.code)
    user_id = get_required_feishu_user_id(profile)
    account_email = build_synthetic_feishu_email(user_id)
    account_id = select_feishu_account_id(profile)

    try:
        user = await user_manager.oauth_callback(
            oauth_name=FEISHU_OAUTH_NAME,
            access_token=token_data["access_token"],
            account_id=account_id,
            account_email=account_email,
            expires_at=token_data.get("expires_at"),
            refresh_token=token_data.get("refresh_token"),
            request=request,
            associate_by_email=True,
            is_verified_by_default=True,
        )
    except exceptions.UserAlreadyExists:
        raise OnyxError(
            OnyxErrorCode.VALIDATION_ERROR,
            "A different Onyx user is already linked to this Feishu account.",
        )

    if not user.is_active:
        raise OnyxError(OnyxErrorCode.UNAUTHORIZED, "User is inactive.")

    await sync_feishu_personal_name(user_manager, user, profile)

    auth_response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, auth_response)

    response = JSONResponse(FeishuLoginResponse(redirect_url=next_url).model_dump())
    for header_name, header_value in auth_response.headers.items():
        if header_name.lower() == "set-cookie":
            response.headers.append(header_name, header_value)

    response.delete_cookie(
        key=FEISHU_STATE_COOKIE_NAME,
        path="/",
        secure=WEB_DOMAIN.startswith("https"),
        httponly=True,
        samesite="lax",
    )
    return response
