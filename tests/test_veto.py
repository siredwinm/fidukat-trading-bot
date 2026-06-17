import json
from types import SimpleNamespace
from signals import veto

SNAP = SimpleNamespace(atr_pct=0.02, direction=1)
MACRO = {"fear_greed": {"value": 50}, "derivatives": {}}


def _clear(monkeypatch):
    for k in ("VETO_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_disabled_without_keys(monkeypatch):
    _clear(monkeypatch)
    assert not veto.enabled()
    assert veto.veto_entry("ETH", SNAP, MACRO) == (False, "no-llm")


def test_chain_order(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VETO_API_KEY", "oc")        # primary (provider default = openai/OpenCode)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")  # backup 1
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")    # backup 2
    chain = veto._chain()
    bases = [b for _, b, _, _ in chain]
    assert bases[0] == veto.BASE_URL                          # OpenCode primary first
    assert "openrouter.ai" in bases[1]                        # then OpenRouter
    assert "api.deepseek.com" in bases[2]                     # then DeepSeek-direct
    assert veto.enabled()


def test_fail_open_when_all_providers_error(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VETO_API_KEY", "oc")
    monkeypatch.setattr(veto, "_request_to",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    v, reason = veto.veto_entry("ETH", SNAP, MACRO)
    assert v is False and reason.startswith("veto-unavailable")  # fails open -> no veto


def test_parses_veto_true(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VETO_API_KEY", "oc")
    monkeypatch.setattr(veto, "_request_to",
                        lambda *a, **k: 'noise {"veto": true, "reason": "blow-off risk"} trailing')
    assert veto.veto_entry("ETH", SNAP, MACRO) == (True, "blow-off risk")


def test_anthropic_base_default(monkeypatch):
    # provider=anthropic must route to api.anthropic.com even if VETO_BASE_URL unset
    monkeypatch.setattr(veto, "PROVIDER", "anthropic")
    monkeypatch.setattr(veto, "BASE_URL", "")
    captured = {}
    monkeypatch.setattr(veto, "_request_to",
                        lambda style, base, *a, **k: captured.update(style=style, base=base) or '{"veto":false,"reason":"ok"}')
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    veto.veto_entry("ETH", SNAP, MACRO)
    assert captured["style"] == "anthropic"
    # base passed empty -> _request_to applies the per-style default (api.anthropic.com)
    assert veto._DEFAULT_BASE["anthropic"] == "https://api.anthropic.com"
