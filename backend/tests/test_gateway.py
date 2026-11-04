import asyncio
import importlib.util
from pathlib import Path


def test_gateway_never_uses_system_proxy_for_loopback_backend(monkeypatch):
    """Loopback upstream traffic must not depend on desktop proxy settings."""
    monkeypatch.setenv("WORKSHOP_GATEWAY_SECRET", "test-secret")
    monkeypatch.setenv("WORKSHOP_BACKEND_URL", "http://127.0.0.1:8003")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    module_path = Path(__file__).parents[2] / "cloudflare" / "gateway.py"
    spec = importlib.util.spec_from_file_location("test_workshop_gateway", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        assert module.client.trust_env is False
    finally:
        asyncio.run(module.client.aclose())
