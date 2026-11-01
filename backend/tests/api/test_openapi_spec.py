"""OpenAPI 3.1 specification tests (EP-41.6 / ALPHA Prompt 52).

Covers the contract promised in docs/openapi.yaml: version, server
variables, the named API-key security scheme, v2 tags, request/response
examples and error responses.

Like the other API tests, the client is created *without* entering the
lifespan context so no database/Redis connections are attempted — the
OpenAPI document is rendered purely from the route definitions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

V2_PATHS = (
    "/v2/chat/completions",
    "/v2/embeddings",
    "/v2/classify",
)


@pytest.fixture
def spec() -> dict:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


class TestOpenApiDocument:
    def test_openapi_version_is_31(self, spec: dict) -> None:
        assert spec["openapi"].startswith("3.1")

    def test_title_and_description_present(self, spec: dict) -> None:
        assert spec["info"]["title"]
        assert "streaming" in spec["info"]["description"].lower()

    def test_all_v2_paths_documented(self, spec: dict) -> None:
        for path in V2_PATHS:
            assert path in spec["paths"], f"missing {path}"

    def test_openapi_tags_include_required_alpha_tags(self, spec: dict) -> None:
        names = {t["name"] for t in spec.get("tags", [])}
        assert {"streaming", "ml", "opa", "vault"} <= names
        # every declared tag carries a description
        for tag in spec.get("tags", []):
            assert tag.get("description"), f"tag {tag['name']} has no description"


class TestServers:
    def test_tenant_server_variable_declared(self, spec: dict) -> None:
        urls = [s["url"] for s in spec["servers"]]
        assert any("{tenant}" in u for u in urls)

        tenant_server = next(s for s in spec["servers"] if "{tenant}" in s["url"])
        variables = tenant_server.get("variables", {})
        assert "tenant" in variables
        assert variables["tenant"].get("default")

    def test_local_dev_server_first(self, spec: dict) -> None:
        assert spec["servers"][0]["url"].startswith("http://localhost")


class TestSecurityScheme:
    def test_api_key_scheme_declared(self, spec: dict) -> None:
        schemes = spec["components"]["securitySchemes"]
        assert "ApiKeyAuth" in schemes
        scheme = schemes["ApiKeyAuth"]
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "bearer"
        assert scheme.get("bearerFormat") == "jwt"
        assert "API key" in scheme["description"]

    def test_v2_endpoints_require_api_key(self, spec: dict) -> None:
        for path in V2_PATHS:
            op = spec["paths"][path]["post"]
            security = op.get("security")
            assert security, f"{path} has no security requirement"
            assert any("ApiKeyAuth" in req for req in security), path


class TestV2EndpointAnnotations:
    def test_streaming_endpoint_tags(self, spec: dict) -> None:
        tags = spec["paths"]["/v2/chat/completions"]["post"]["tags"]
        assert "streaming" in tags
        assert "v2" in tags

    def test_ml_endpoints_tags(self, spec: dict) -> None:
        for path in ("/v2/embeddings", "/v2/classify"):
            tags = spec["paths"][path]["post"]["tags"]
            assert "ml" in tags, path
            assert "v2" in tags, path

    def test_error_responses_documented(self, spec: dict) -> None:
        for path in V2_PATHS:
            responses = spec["paths"][path]["post"]["responses"]
            for status in ("401", "429", "500"):
                assert status in responses, f"{path} missing {status}"

    def test_chat_completions_sse_response_documented(self, spec: dict) -> None:
        responses = spec["paths"]["/v2/chat/completions"]["post"]["responses"]
        assert "200" in responses
        content = responses["200"]["content"]
        assert "text/event-stream" in content
        examples = content["text/event-stream"]["examples"]
        assert {"chunk", "blocked", "complete"} <= set(examples)

    def test_chat_completions_prompt_block_documented(self, spec: dict) -> None:
        responses = spec["paths"]["/v2/chat/completions"]["post"]["responses"]
        assert "403" in responses
        assert "413" in responses  # DoS protection

    def test_request_examples_present(self, spec: dict) -> None:
        for path in V2_PATHS:
            request_body = spec["paths"][path]["post"]["requestBody"]
            examples = request_body["content"]["application/json"].get("examples")
            assert examples, f"{path} has no request examples"

    def test_response_models_declared(self, spec: dict) -> None:
        schemas = spec["components"]["schemas"]
        assert "EmbeddingResponse" in schemas
        assert "ClassificationResponse" in schemas

        embeddings = spec["paths"]["/v2/embeddings"]["post"]
        ref = embeddings["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert "EmbeddingResponse" in ref

        classify = spec["paths"]["/v2/classify"]["post"]
        ref = classify["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert "ClassificationResponse" in ref
