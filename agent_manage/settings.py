"""Central configuration for model gateway environments and URL construction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from urllib.parse import quote, unquote, urlparse

DEFAULT_MODEL_ENV = "global"
DEFAULT_AI_SHOP = "shop"
DEFAULT_IMAGE_QUALITY = "low"
IMAGE_QUALITY_CHOICES = ("low", "medium", "high", "auto")


@dataclass(frozen=True)
class ModelGateway:
    """A model gateway host with derived API and catalog endpoints."""

    origin: str

    @property
    def base_url(self) -> str:
        return f"{self.origin}/aigateway/v1"

    @property
    def catalog_url(self) -> str:
        return f"{self.origin}/aigateway/api/frontend/aimodels/byProvider"

    def model_base_url(self, shop: str | None = None) -> str:
        resolved_shop = normalize_shop(shop)
        return f"{self.origin}/aigateway/{quote(resolved_shop, safe='')}/v1"

    def model_catalog_url(self, shop: str | None = None) -> str:
        resolved_shop = normalize_shop(shop)
        return f"{self.catalog_url}/{quote(resolved_shop, safe='')}"

    def as_legacy_dict(self) -> dict[str, str]:
        """Keep the historical public mapping shape used by callers."""

        return {
            "base_url": self.base_url,
            "catalog_url": self.catalog_url,
        }


MODEL_GATEWAY_CONFIGS: Mapping[str, ModelGateway] = MappingProxyType(
    {
        "global": ModelGateway("https://api.dola.io"),
        "test": ModelGateway("https://unitag.dola.fi"),
        "cn": ModelGateway("https://api.dolaio.cn"),
    }
)

# Compatibility view retaining the historical mutable dict shape.
MODEL_GATEWAYS: dict[str, dict[str, str]] = {
    name: gateway.as_legacy_dict()
    for name, gateway in MODEL_GATEWAY_CONFIGS.items()
}


def normalize_shop(shop: str | None) -> str:
    resolved = (shop or DEFAULT_AI_SHOP).strip().strip("/")
    if not resolved:
        return DEFAULT_AI_SHOP
    if (
        resolved in {".", ".."}
        or "/" in resolved
        or "\\" in resolved
        or any(ord(character) < 32 for character in resolved)
    ):
        raise ValueError(f"Invalid model shop path '{shop}': expected one safe segment")
    return resolved


def normalize_image_quality(quality: str | None) -> str:
    resolved = (quality or DEFAULT_IMAGE_QUALITY).strip().lower()
    if resolved not in IMAGE_QUALITY_CHOICES:
        allowed = ", ".join(IMAGE_QUALITY_CHOICES)
        raise ValueError(f"Unsupported image quality '{quality}'. Allowed: {allowed}")
    return resolved


def normalize_public_base_url(base_url: str | None) -> str | None:
    if base_url is None or not base_url.strip():
        return None

    resolved = base_url.strip()
    if any(ord(character) < 32 for character in resolved):
        raise ValueError("Invalid base_url: control characters are not allowed")

    parsed = urlparse(resolved)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Invalid base_url: expected an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Invalid base_url: credentials are not allowed")
    if not re.fullmatch(r"[A-Za-z0-9.:[\]-]+", parsed.netloc):
        raise ValueError("Invalid base_url: hostname or port contains unsafe characters")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Invalid base_url: port must be numeric and valid") from exc
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(
            "Invalid base_url: expected a site root without path, query, or fragment"
        )

    return f"{parsed.scheme.lower()}://{parsed.netloc}/"


def catalog_url_for_shop(catalog_url: str, shop: str | None = None) -> str:
    base_catalog_url = catalog_url.rstrip("/")
    if not base_catalog_url.endswith("/byProvider"):
        raise ValueError("--ai-shop is only supported for byProvider model catalog URLs")
    return f"{base_catalog_url}/{quote(normalize_shop(shop), safe='')}"


def model_base_url_for_shop(base_url: str, shop: str | None = None) -> str:
    normalized_base_url = base_url.rstrip("/")
    if not normalized_base_url.endswith("/v1"):
        raise ValueError("--ai-shop model baseUrl must end with /v1")
    return f"{normalized_base_url[:-3]}/{quote(normalize_shop(shop), safe='')}/v1"


def same_url_host(left: str, right: str) -> bool:
    left_url = urlparse(left)
    right_url = urlparse(right)
    left_host = left_url.hostname
    right_host = right_url.hostname
    if not left_host or not right_host:
        return False

    def effective_port(parsed) -> int | None:
        if parsed.port is not None:
            return parsed.port
        return {"http": 80, "https": 443}.get(parsed.scheme.lower())

    return (
        left_host.lower() == right_host.lower()
        and effective_port(left_url) == effective_port(right_url)
    )


def _is_model_gateway_path(base_url: str) -> bool:
    parts = [part for part in urlparse(base_url).path.split("/") if part]
    return (
        len(parts) in {2, 3}
        and parts[0].lower() == "aigateway"
        and parts[-1] == "v1"
    )


def gateway_for_base_url(base_url: str) -> dict[str, str]:
    for gateway in MODEL_GATEWAYS.values():
        if gateway["base_url"] == base_url or (
            same_url_host(gateway["base_url"], base_url)
            and _is_model_gateway_path(base_url)
        ):
            return gateway
    raise ValueError(f"Unsupported model baseUrl '{base_url}'")


def shop_from_model_base_url(base_url: str) -> str | None:
    parts = [unquote(part) for part in urlparse(base_url).path.split("/") if part]
    if len(parts) >= 3 and parts[-3].lower() == "aigateway" and parts[-1] == "v1":
        return parts[-2]
    return None
