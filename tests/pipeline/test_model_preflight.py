from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_arxiv_daily.pipeline import model_preflight
from zotero_arxiv_daily.pipeline.model_preflight import (
    CpuVisionRuntimeError,
    prepare_local_reranker,
    require_cpu_vision_runtime,
)


class _FakeProvider:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_preflight_loads_pinned_public_reranker_without_remote_code():
    captured: list[_FakeProvider] = []
    events: list[str] = []

    def factory(**kwargs):
        events.append("model")
        provider = _FakeProvider(**kwargs)
        captured.append(provider)
        return provider

    prepare_local_reranker(
        Path(__file__).parents[2] / "config",
        provider_factory=factory,
        runtime_probe=lambda: events.append("runtime"),
    )

    assert events == ["runtime", "model"]
    assert captured[0].kwargs["model"] == "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
    assert captured[0].kwargs["revision"] == "b207367332321f8e44f96e224ef15bc607f4dbf0"
    assert captured[0].kwargs["cache_folder"] == Path("models/reranker")
    assert captured[0].kwargs["task"] == "retrieval"
    assert captured[0].kwargs["trust_remote_code"] is False
    assert captured[0].kwargs["prompt_name"] is None
    assert captured[0].kwargs["encode_kwargs"] == {"normalize_embeddings": True}
    assert captured[0].kwargs["max_sequence_length"] == 512
    assert captured[0].closed is True


def test_cli_reports_only_safe_preflight_error_code(capsys):
    private_detail = "private-hub-response-body"

    def fail(_config_dir):
        raise RuntimeError(private_detail)

    with pytest.raises(SystemExit) as captured:
        model_preflight.main(["--config-dir", "config"], preparer=fail)

    assert captured.value.code == 2
    stderr = capsys.readouterr().err
    assert "error_code=reranker_runtime_failed" in stderr
    assert private_detail not in stderr


def test_cpu_vision_probe_wraps_dynamic_import_failures():
    private_detail = "missing-private-native-library-path"

    def importer(name):
        if name == "torch":
            return object()
        raise ImportError(private_detail)

    with pytest.raises(CpuVisionRuntimeError) as captured:
        require_cpu_vision_runtime(importer=importer)

    assert private_detail not in str(captured.value)


def test_cpu_vision_probe_executes_native_nms_on_empty_cpu_tensors():
    events = []
    float32 = object()

    def empty(shape, *, dtype):
        events.append(("empty", shape, dtype))
        return SimpleNamespace(shape=shape)

    def nms(boxes, scores, threshold):
        events.append(("nms", boxes.shape, scores.shape, threshold))
        return SimpleNamespace(numel=lambda: 0)

    modules = {
        "torch": SimpleNamespace(
            version=SimpleNamespace(cuda=None),
            float32=float32,
            empty=empty,
        ),
        "torchvision": object(),
        "torchvision.ops": SimpleNamespace(nms=nms),
    }

    require_cpu_vision_runtime(importer=modules.__getitem__)

    assert events == [
        ("empty", (0, 4), float32),
        ("empty", (0,), float32),
        ("nms", (0, 4), (0,), 0.5),
    ]


def test_cli_reports_fixed_cpu_vision_error_without_dynamic_detail(capsys):
    private_detail = "missing-private-native-library-path"

    def fail(_config_dir):
        try:
            raise ImportError(private_detail)
        except ImportError as error:
            raise CpuVisionRuntimeError from error

    with pytest.raises(SystemExit) as captured:
        model_preflight.main(["--config-dir", "config"], preparer=fail)

    assert captured.value.code == 2
    stderr = capsys.readouterr().err
    assert "error_code=cpu_vision_runtime_failed" in stderr
    assert private_detail not in stderr
