"""models/quantification 显式模型回归（Batch 2 收尾）。

验证 ParseRun / QuantifyRun / DocumentVersion 为显式 Pydantic 字段契约：
- 无 extra=allow 透传（未知字段被拒绝）；
- 关键 provenance 字段存在且可序列化；
- 时间戳/枚举默认语义正确。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.quantification import DocumentVersion, ParseRun, QuantifyRun


def test_parse_run_carries_provenance_and_serializes():
    r = ParseRun(
        run_id="parse_1",
        document_id="doc1",
        source_revision="rev-abc",
        parser="docling",
        doc_version=3,
        status="COMPLETED",
        chars_parsed=1234,
        pages=9,
        output_format="blocks",
        manifest={"blocks": 42},
    )
    data = r.model_dump(mode="json")
    assert data["run_id"] == "parse_1"
    assert data["doc_version"] == 3
    assert data["status"] == "COMPLETED"
    assert data["manifest"] == {"blocks": 42}
    assert data["started_at"]  # 默认时间戳


def test_quantify_run_carries_claims_evidence_metrics_and_replay():
    r = QuantifyRun(
        run_id="quant_1",
        quantize_round=2,
        source_document_id="doc1",
        input_hash="h1",
        parser="splitter",
        model="deepseek",
        status="NEEDS_REVIEW",
        claims=["c1", "c2"],
        evidence_refs=["e1"],
        metrics={"coverage": 0.8},
        reviewer="author",
        replay=True,
    )
    data = r.model_dump(mode="json")
    assert data["claims"] == ["c1", "c2"]
    assert data["evidence_refs"] == ["e1"]
    assert data["metrics"] == {"coverage": 0.8}
    assert data["model"] == "deepseek"
    assert data["replay"] is True


def test_quantify_run_requires_run_and_source():
    with pytest.raises(ValidationError):
        QuantifyRun()  # 缺 run_id / source_document_id


def test_document_version_version_and_hash_required():
    v = DocumentVersion(version=2, document_id="doc1", snapshot_hash="sha256:xx", parser="docling")
    assert v.model_dump(mode="json")["version"] == 2
    with pytest.raises(ValidationError):
        DocumentVersion(document_id="doc1")  # 缺 version / snapshot_hash


def test_models_do_not_allow_unknown_fields(no_extra_allow_models=None):
    """显式模型不得静默透传未知字段（禁用 extra=allow 透传）。"""
    for factory, payload in (
        (ParseRun, {"run_id": "r", "document_id": "d", "unexpected": 1}),
        (QuantifyRun, {"run_id": "r", "source_document_id": "d", "unexpected": 1}),
        (DocumentVersion, {"version": 1, "document_id": "d", "snapshot_hash": "h", "unexpected": 1}),
    ):
        with pytest.raises(ValidationError):
            factory(**payload)


def test_quantify_status_enum_literal():
    with pytest.raises(ValidationError):
        QuantifyRun(run_id="r", source_document_id="d", status="BOGUS")