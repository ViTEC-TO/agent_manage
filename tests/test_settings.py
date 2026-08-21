import unittest

from agent_manage.models import CreateInstanceRequest
from agent_manage.orchestrator import InstanceManagerV2
from agent_manage.settings import (
    DEFAULT_AI_SHOP,
    DEFAULT_MODEL_ENV,
    MODEL_GATEWAY_CONFIGS,
    MODEL_GATEWAYS,
    catalog_url_for_shop,
    gateway_for_base_url,
    model_base_url_for_shop,
    normalize_image_quality,
    normalize_shop,
    same_url_host,
    shop_from_model_base_url,
)


class ModelGatewaySettingsTest(unittest.TestCase):
    def test_defaults_are_global_and_shop(self):
        self.assertEqual(DEFAULT_MODEL_ENV, "global")
        self.assertEqual(DEFAULT_AI_SHOP, "shop")
        self.assertEqual(CreateInstanceRequest().model_env, "global")
        self.assertEqual(CreateInstanceRequest().ai_shop, "shop")
        self.assertEqual(
            InstanceManagerV2.MODEL_CATALOG_URL,
            "https://api.dola.io/aigateway/api/frontend/aimodels/byProvider/shop",
        )
        self.assertEqual(normalize_shop(None), "shop")
        self.assertEqual(normalize_shop(" / "), "shop")
        self.assertEqual(normalize_image_quality(None), "low")
        self.assertEqual(normalize_image_quality(" LOW "), "low")

    def test_gateway_origins_are_defined_once_and_derive_legacy_urls(self):
        expected_origins = {
            "global": "https://api.dola.io",
            "test": "https://unitag.dola.fi",
            "cn": "https://api.dolaio.cn",
        }
        self.assertEqual(
            {name: gateway.origin for name, gateway in MODEL_GATEWAY_CONFIGS.items()},
            expected_origins,
        )
        for name, origin in expected_origins.items():
            with self.subTest(name=name):
                self.assertEqual(MODEL_GATEWAYS[name]["base_url"], f"{origin}/aigateway/v1")
                self.assertEqual(
                    MODEL_GATEWAYS[name]["catalog_url"],
                    f"{origin}/aigateway/api/frontend/aimodels/byProvider",
                )

    def test_shop_urls_default_to_shop_for_every_environment(self):
        for name, gateway in MODEL_GATEWAY_CONFIGS.items():
            with self.subTest(name=name):
                self.assertEqual(
                    catalog_url_for_shop(gateway.catalog_url),
                    f"{gateway.origin}/aigateway/api/frontend/aimodels/byProvider/shop",
                )
                self.assertEqual(
                    model_base_url_for_shop(gateway.base_url),
                    f"{gateway.origin}/aigateway/shop/v1",
                )

    def test_custom_shop_is_normalized_and_url_encoded(self):
        gateway = MODEL_GATEWAY_CONFIGS["global"]
        self.assertEqual(
            catalog_url_for_shop(gateway.catalog_url, "/team shop/"),
            f"{gateway.catalog_url}/team%20shop",
        )
        self.assertEqual(
            model_base_url_for_shop(gateway.base_url, "/team shop/"),
            f"{gateway.origin}/aigateway/team%20shop/v1",
        )
        for unsafe_shop in (".", "..", "team/shop", "team\\shop"):
            with self.subTest(unsafe_shop=unsafe_shop):
                with self.assertRaisesRegex(ValueError, "safe segment"):
                    normalize_shop(unsafe_shop)

    def test_gateway_and_shop_can_be_recovered_from_configured_base_url(self):
        global_gateway = MODEL_GATEWAY_CONFIGS["global"]
        configured = global_gateway.model_base_url("shop")
        self.assertEqual(gateway_for_base_url(configured), MODEL_GATEWAYS["global"])
        self.assertEqual(shop_from_model_base_url(configured), "shop")
        self.assertTrue(same_url_host(configured, global_gateway.base_url))
        self.assertFalse(
            same_url_host(configured, MODEL_GATEWAY_CONFIGS["cn"].base_url)
        )
        self.assertTrue(
            same_url_host(
                "https://API.DOLA.IO:443/aigateway/shop/v1",
                global_gateway.base_url,
            )
        )
        with self.assertRaisesRegex(ValueError, "Unsupported model baseUrl"):
            gateway_for_base_url(f"{global_gateway.origin}/unrelated/v1")


if __name__ == "__main__":
    unittest.main()
