"""Ranking semantics, exact evidence, version selection and scope are consequential."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import yaml

from dismech_evals.products import build_products, history_rows, metadata
from dismech_evals.site import build_site

ROOT = Path(__file__).resolve().parents[1]


def row(disease, section, index, labels=("MATCH",), evidence=0, status="assessed"):
    paths = ["/", "/assertion/description", "/about/disease"]
    answers = {
        path: {
            "label": label,
            "confidence": 0.8,
            "probabilities": {
                k: 0.8 if k == label else 0.1 for k in ("MATCH", "MISMATCH", "PARTIAL")
            },
        }
        for path, label in zip(paths, labels, strict=False)
    }
    return {
        "file": f"kb/disorders/{disease}.yaml",
        "disease": disease,
        "section": section,
        "assertion_path": f"/{section}/{index}",
        "evidence_path": f"/{section}/{index}/evidence/{evidence}",
        "reference": "PMID:1",
        "status": status,
        "assessed_at": "2026-09-24T00:00:00Z",
        "cache_key": "k",
        "claim": {
            "about": {"disease": {"name": disease}},
            "assertion_type": "Phenotype",
            "assertion": {"name": "Fever"},
            "selected_evidence": {
                "snippet": "Exact excerpt <script>alert(1)</script>",
                "supports": "SUPPORT",
            },
        },
        "result": {"answers": answers, "model": "jev-test"},
    }


def meta(**kwargs):
    return metadata(
        {"source_revision": "a" * 40, "model": "jev-test", "complete": True, **kwargs}
    )


def test_distinct_assertions_category_ranking_and_complete_handoff(tmp_path):
    rows = [
        row("A", "phenotypes", 0, ("MISMATCH", "MISMATCH", "PARTIAL")),
        row("A", "phenotypes", 0, ("MISMATCH",), evidence=1),
        row("B", "treatments", 0, ("MISMATCH",)),
        row("B", "treatments", 1, ("MISMATCH",)),
        row("C", "phenotypes", 0, ("PARTIAL",)),
        row("D", "phenotypes", 0),
        row("E", "phenotypes", 0, status="api_error"),
    ]
    data, queues = build_products(
        lambda: iter(rows), meta(), tmp_path, top_n=1, max_findings=1
    )
    assert data["totals"]["mismatch_assertions"] == 3
    assert data["totals"]["assessed_assertions"] == 5
    assert data["totals"]["statuses"]["api_error"] == 1
    assert queues["overall"][0]["disease"] == "B"
    assert queues["phenotypes"][0]["disease"] == "A"
    assert queues["phenotypes"][0]["metrics"]["mismatch_assertions"] == 1
    assert queues["phenotypes"][0]["findings_truncated"]
    assert queues["phenotypes"][0]["findings_total"] == 2
    assert all(
        f["section"] == "phenotypes" for f in queues["phenotypes"][0]["findings"]
    )
    assert queues["phenotypes"][0]["findings"][0]["claim"] == rows[0]["claim"]
    assert queues["phenotypes"][0]["findings"][0]["source_revision"] == "a" * 40
    schema = json.loads((ROOT / "schemas/recuration.schema.json").read_text())
    for queue in queues.values():
        for item in queue:
            jsonschema.validate(item, schema)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    for file, info in manifest["files"].items():
        assert (
            hashlib.sha256((tmp_path / file).read_bytes()).hexdigest() == info["sha256"]
        )
    before = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    build_products(lambda: iter(rows), meta(), tmp_path, top_n=1, max_findings=1)
    assert before == {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }


def test_empty_unassessed_scope_and_stale_category_cleanup(tmp_path):
    build_products(
        lambda: iter([row("A", "phenotypes", 0, ("PARTIAL",))]), meta(), tmp_path
    )
    d, q = build_products(
        lambda: iter([row("A", "treatments", 0, status="ready")]),
        meta(dry_run=True, limit=1),
        tmp_path,
    )
    assert d["totals"]["mismatch_fraction"] is None
    assert not d["provenance"]["corpus_complete"]
    assert not q["overall"] and not q["treatments"]
    assert not (tmp_path / "categories/phenotypes.jsonl").exists()
    assert not meta(complete=False)["corpus_complete"]
    assert not meta(sections=["phenotypes"])["corpus_complete"]
    assert not meta(inputs=["kb/disorders/A.yaml"])["corpus_complete"]
    assert meta()["corpus_complete"]


def test_history_excludes_retired_and_wrong_model_and_uses_latest_refresh(tmp_path):
    original = yaml.safe_load(
        (
            ROOT / "analysis/classification/jev/Asthma/evidence_claim_match.yaml"
        ).read_text()
    )
    key, assessment = next(iter(original["assessments"].items()))
    identity = assessment["input_sha256"]
    original["assessments"] = {key: assessment}
    original["inputs"] = {identity: original["inputs"][identity]}
    newer = deepcopy(assessment)
    newer["assessed_at"] = "2026-09-24T12:00:00Z"
    newer["result"]["answers"]["/"]["label"] = "MISMATCH"
    assessment["reassessments"] = [newer]
    target = tmp_path / "Asthma/evidence_claim_match.yaml"
    target.parent.mkdir()
    target.write_text(yaml.safe_dump(original))
    rows = list(history_rows(tmp_path, assessment["model"]))
    assert len(rows) == 1
    assert rows[0]["assessed_at"] == newer["assessed_at"]
    assert rows[0]["result"]["answers"]["/"]["label"] == "MISMATCH"
    assert not list(history_rows(tmp_path, "different-model"))
    original["inputs"][identity]["active"] = False
    target.write_text(yaml.safe_dump(original))
    assert not list(history_rows(tmp_path, assessment["model"]))


def test_site_escapes_embedded_data_and_serves_relative_downloads(tmp_path):
    product, output = tmp_path / "products", tmp_path / "site"
    build_products(
        lambda: iter(
            [row("</script><script>bad()</script>", "phenotypes", 0, ("PARTIAL",))]
        ),
        meta(),
        product,
    )
    build_site(product, output)
    assert "</script>" not in (output / "data.js").read_text()
    assert all(
        "</script>" not in p.read_text() for p in (output / "packets").glob("*.js")
    )
    for page in ("index", "entries", "categories", "agents", "method", "entry"):
        text = (output / (page + ".html")).read_text()
        assert 'src="data.js"' in text
        assert 'href="site.css"' in text
    assert (output / "downloads/top.jsonl").read_bytes() == (
        product / "top.jsonl"
    ).read_bytes()


def test_handoff_does_not_present_citation_explanation_as_model_context(tmp_path):
    item = row("A", "phenotypes", 0, ("PARTIAL",))
    item["claim"]["selected_evidence"]["explanation"] = "Curator's own interpretation"
    _, queues = build_products(lambda: iter([item]), meta(), tmp_path)
    evidence = queues["overall"][0]["findings"][0]["claim"]["selected_evidence"]
    assert "explanation" not in evidence
    assert evidence["snippet"] == item["claim"]["selected_evidence"]["snippet"]
    assert (
        item["claim"]["selected_evidence"]["explanation"]
        == "Curator's own interpretation"
    )
