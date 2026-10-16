"""Production runtime contract for FuzeQuality's LiteLLM model route."""

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
VALUES = ROOT / "helm/litellm/values.yaml"
PROD_VALUES = ROOT / "helm/litellm/values-contabo.yaml"
SMOKE_TEMPLATE = ROOT / "helm/litellm/templates/job-provider-smoke.yaml"


def _values() -> dict:
    return yaml.safe_load(VALUES.read_text())


def test_fuzequality_model_alias_is_served_and_has_cross_provider_fallbacks():
    values = _values()
    models = {model["name"]: model for model in values["models"]}
    assert models["quality-analysis"] == {
        "name": "quality-analysis",
        "provider": "anthropic/claude-sonnet-4-6",
        "keyEnv": "ANTHROPIC_API_KEY",
    }
    for key in ("fallbacks", "context_window_fallbacks"):
        routes = {
            primary: alternatives
            for route in values["routerSettings"][key]
            for primary, alternatives in route.items()
        }
        assert routes["quality-analysis"] == ["gpt-4.1", "gemini-flash-latest"]


def test_production_enables_runtime_provider_smoke():
    prod = yaml.safe_load(PROD_VALUES.read_text())
    assert prod["providerSmoke"]["enabled"] is True


def test_runtime_smoke_checks_direct_anthropic_and_litellm_without_printing_keys():
    template = SMOKE_TEMPLATE.read_text()
    assert "argocd.argoproj.io/hook\": PostSync" in template
    assert "https://api.anthropic.com/v1/models?limit=1" in template
    assert "http://litellm:{{ .Values.port }}/v1/chat/completions" in template
    assert 'key: ANTHROPIC_API_KEY' in template
    assert 'key: LITELLM_MASTER_KEY' in template
    assert 'include "litellm.selectorLabels"' not in template
    assert "readOnlyRootFilesystem: true" in template
    assert "emptyDir: {}" in template
    assert "echo ${ANTHROPIC_API_KEY}" not in template
    assert "echo ${LITELLM_MASTER_KEY}" not in template
