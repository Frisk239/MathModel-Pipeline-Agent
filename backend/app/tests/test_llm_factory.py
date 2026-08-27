"""LLMFactory 单测：四 Agent 配置映射与评审模型回退（不触网）。"""

from app.config.setting import settings
from app.core.llm.llm_factory import LLMFactory


class _RecordingLLM:
    instances: list["_RecordingLLM"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _RecordingLLM.instances.append(self)


def _patch_llm(monkeypatch):
    _RecordingLLM.instances = []
    monkeypatch.setattr("app.core.llm.llm_factory.LLM", _RecordingLLM)


def test_all_llms_map_settings_per_role(monkeypatch):
    _patch_llm(monkeypatch)
    keys = {
        "COORDINATOR": "k-c",
        "MODELER": "k-m",
        "CODER": "k-d",
        "WRITER": "k-w",
    }
    models = {
        "COORDINATOR": "m-c",
        "MODELER": "m-m",
        "CODER": "m-d",
        "WRITER": "m-w",
    }
    for role in keys:
        monkeypatch.setattr(settings, f"{role}_API_KEY", keys[role])
        monkeypatch.setattr(settings, f"{role}_MODEL", models[role])

    llms = LLMFactory("t-map").get_all_llms()

    assert [llm.kwargs["api_key"] for llm in llms] == ["k-c", "k-m", "k-d", "k-w"]
    assert [llm.kwargs["model"] for llm in llms] == ["m-c", "m-m", "m-d", "m-w"]
    assert all(llm.kwargs["task_id"] == "t-map" for llm in llms)


def test_review_llm_falls_back_to_coordinator(monkeypatch):
    _patch_llm(monkeypatch)
    monkeypatch.setattr(settings, "REVIEW_MODEL", None)
    monkeypatch.setattr(settings, "REVIEW_API_KEY", None)
    monkeypatch.setattr(settings, "COORDINATOR_MODEL", "coord-main")
    monkeypatch.setattr(settings, "COORDINATOR_API_KEY", "k-c")

    llm = LLMFactory("t-fallback").get_review_llm()

    assert llm.kwargs["model"] == "coord-main"
    assert llm.kwargs["api_key"] == "k-c"


def test_review_llm_prefers_own_config(monkeypatch):
    _patch_llm(monkeypatch)
    monkeypatch.setattr(settings, "REVIEW_MODEL", "review-1")
    monkeypatch.setattr(settings, "REVIEW_API_KEY", "k-r")
    monkeypatch.setattr(settings, "COORDINATOR_MODEL", "coord-main")

    llm = LLMFactory("t-review").get_review_llm()

    assert llm.kwargs["model"] == "review-1"
    assert llm.kwargs["api_key"] == "k-r"
