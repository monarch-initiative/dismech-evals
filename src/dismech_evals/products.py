"""Deterministic recuration queues derived from saved judgments; no inference."""

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

import yaml

VERSION = 1
RANKING = (
    "Distinct assertions with MISMATCH descending; maximum MISMATCH probability "
    "descending when a mismatch exists; distinct assertions with PARTIAL descending; "
    "missing evidence/snippets descending; source file ascending."
)
GUIDANCE = [
    "Check the current dismech file and its repository curation instructions first.",
    "Resolve the YAML pointers and compare the current assertion and snippet with the "
    "saved claim. If they have changed, treat the finding as stale and reassess.",
    "Evaluate whether this snippet supports the complete assertion, including disease, "
    "subtype, qualifiers, support/refutation and directness. Do not evaluate source quality.",
    "MISMATCH and PARTIAL are model signals, not curator verdicts. PARTIAL may mean weak, "
    "incomplete, mixed or uncertain support; it does not establish a false claim.",
    "Inspect other evidence before changing an assertion: a snippet-level gap may be "
    "covered by another citation. Parent evidence need not cover independently evidenced edges.",
    "When context is missing, consult the reference cache and consider extending the "
    "snippet with supported context, ellipses or bracketed editorial text. Follow dismech "
    "reference validation and YAML normalization instructions; never rewrite cached predictions.",
    "Validate any curation edits and report what changed, what remains uncertain, and why.",
]
LABELS = ("MISMATCH", "PARTIAL", "MATCH")


def jsonl(path):
    with path.open() as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def source_url(file, revision):
    return (
        "https://github.com/monarch-initiative/dismech/blob/"
        + quote(revision or "main", safe="")
        + "/"
        + quote(file, safe="/")
    )


def history_rows(root, model):
    """Read the public v2 cache as observed; never infer activity against today's KB."""
    for path in sorted(root.glob("*/evidence_claim_match.yaml")):
        doc = yaml.safe_load(path.read_text())
        if doc.get("format_version") != 2:
            raise ValueError(f"Expected migrated cache format 2: {path}")
        inv = doc.get("inventory", {})
        if not inv.get("complete") or not inv.get("source_exists", True):
            continue
        selected = {}
        for key, record in doc["assessments"].items():
            if record["model"] != model:
                continue
            outcome = (record.get("reassessments") or [record])[-1]
            identity = record["input_sha256"]
            candidate = (outcome["assessed_at"], key, record, outcome)
            if identity not in selected or candidate[:2] > selected[identity][:2]:
                selected[identity] = candidate
        for identity, (_, key, record, outcome) in selected.items():
            item = doc["inputs"][identity]
            if not item.get("active"):
                continue
            for occurrence in item.get("occurrences", []):
                assertion = occurrence["assertion_path"]
                yield {
                    "file": doc["source_file"],
                    "disease": doc["disease"],
                    "section": assertion.split("/")[1],
                    **occurrence,
                    "claim": item["claim"],
                    "cache_key": key,
                    "input_sha256": identity,
                    "configuration_sha256": record["configuration_sha256"],
                    "source_revision": inv.get("source_revision"),
                    "active_as_of": item.get("active_as_of"),
                    "cached": True,
                    **outcome,
                }


def metadata(manifest, *, run_id=None, mode="run"):
    shards = manifest.get("shards", [])
    evaluation = manifest.get("evaluation") or (
        shards[0].get("evaluation", {}) if shards else {}
    )
    complete = bool(manifest.get("complete"))
    limited = bool(
        manifest.get("limit") or manifest.get("sections") or manifest.get("inputs")
    )
    return {
        "mode": mode,
        "run_id": run_id,
        "run_url": f"https://github.com/monarch-initiative/dismech-evals/actions/runs/{run_id.split('-')[0]}"
        if run_id
        else None,
        "source_revision": manifest.get("source_revision"),
        "classifier_revision": evaluation.get("classifier_revision"),
        "evaluation_revision": evaluation.get("evaluation_revision"),
        "model": manifest.get("model"),
        "complete": complete,
        "corpus_complete": complete
        and not limited
        and not manifest.get("dry_run")
        and mode == "run",
        "dry_run": bool(manifest.get("dry_run")),
        "scope": {
            k: manifest.get(k)
            for k in ("limit", "sections", "inputs", "missing_shards")
        },
        "started_at": manifest.get("started_at")
        or min((s["started_at"] for s in shards if s.get("started_at")), default=None),
        "finished_at": manifest.get("finished_at")
        or max(
            (s["finished_at"] for s in shards if s.get("finished_at")), default=None
        ),
    }


def group_for(path):
    if path == "/":
        return "Whole claim"
    if path.startswith("/about/"):
        return "About / disease & subtype"
    parts = path.split("/")
    if any(
        p in parts
        for p in ("temporality", "frequency", "severity", "onset", "sex", "diagnostic")
    ):
        return "Qualifiers"
    if parts[-1] in ("term", "preferred_term", "name"):
        return "Terms & names"
    if parts[-1] in ("description", "notes", "explanation"):
        return "Descriptions"
    return "Other fields"


class Metrics:
    def __init__(self):
        self.assertions = set()
        self.assessed = set()
        self.mismatches = set()
        self.partials = set()
        self.statuses = Counter()
        self.root = Counter()
        self.aspects = Counter()
        self.groups = defaultdict(Counter)
        self.max_p = 0
        self.flagged_pairs = 0

    def add(self, row):
        identity = (row["file"], row.get("assertion_path", ""))
        if identity[1]:
            self.assertions.add(identity)
        self.statuses[row["status"]] += 1
        if row["status"] != "assessed":
            return
        self.assessed.add(identity)
        flagged = False
        for path, answer in row["result"]["answers"].items():
            label = answer["label"]
            if label not in LABELS:
                raise ValueError(f"Unknown model label: {label}")
            (self.root if path == "/" else self.aspects)[label] += 1
            self.groups[group_for(path)][label] += 1
            self.max_p = max(self.max_p, answer["probabilities"]["MISMATCH"])
            if label == "MISMATCH":
                self.mismatches.add(identity)
                flagged = True
            elif label == "PARTIAL":
                self.partials.add(identity)
                flagged = True
        self.flagged_pairs += flagged

    def dump(self):
        n = len(self.assessed)
        return {
            "assertions": len(self.assertions),
            "assessed_assertions": n,
            "pairs": sum(self.statuses.values()),
            "assessed_pairs": self.statuses["assessed"],
            "mismatch_assertions": len(self.mismatches),
            "partial_assertions": len(self.partials),
            "mismatch_fraction": len(self.mismatches) / n if n else None,
            "max_p_mismatch": self.max_p,
            "flagged_pairs": self.flagged_pairs,
            "statuses": dict(self.statuses),
            "root": {k: self.root[k] for k in LABELS},
            "aspects": {k: self.aspects[k] for k in LABELS},
            "aspect_groups": {
                g: {k: c[k] for k in LABELS} for g, c in sorted(self.groups.items())
            },
        }


def rank_key(entry):
    m = entry["metrics"]
    return (
        -m["mismatch_assertions"],
        -m["max_p_mismatch"] if m["mismatch_assertions"] else 0,
        -m["partial_assertions"],
        -m["statuses"].get("no_evidence", 0) - m["statuses"].get("missing_snippet", 0),
        entry["file"],
    )


def finding(row, meta):
    claim = row["claim"]
    state = {
        k: claim[k]
        for k in ("about", "assertion_type", "assertion", "selected_evidence")
    }
    source_revision = row.get("source_revision") or meta["source_revision"]
    # Cache keys refer to saved assessments; the snapshot is copied, never reinterpreted.
    return {
        "assessment_key": row.get("cache_key"),
        "input_sha256": row.get("input_sha256"),
        "configuration_sha256": row.get("configuration_sha256"),
        "section": row["section"],
        "assertion_path": row["assertion_path"],
        "evidence_path": row["evidence_path"],
        "reference": row.get("reference"),
        "claim": state,
        "answers": row["result"]["answers"],
        "model": row["result"]["model"],
        "assessed_at": row.get("assessed_at"),
        "source_revision": source_revision,
        "source_url": source_url(row["file"], source_revision),
        "active_as_of": row.get("active_as_of"),
    }


def finding_key(item):
    answers = item["answers"].values()
    mismatch = any(a["label"] == "MISMATCH" for a in answers)
    return (
        not mismatch,
        -max(a["probabilities"]["MISMATCH"] for a in answers),
        item["assertion_path"],
        item["evidence_path"],
    )


def build_products(rows, meta, output, top_n=25, max_findings=10):
    """Two streaming passes: aggregate rankings, then retain bounded evidence packets."""
    if top_n < 1 or max_findings < 1:
        raise ValueError("Queue and finding limits must be positive")
    output.mkdir(parents=True, exist_ok=True)
    totals = Metrics()
    entries, categories = {}, {}
    snapshots, assessed_dates = set(), set()
    for row in rows():
        file, category = row["file"], row.get("section") or "unclassified"
        item = entries.setdefault(
            file, {"disease": row["disease"], "all": Metrics(), "categories": {}}
        )
        section = item["categories"].setdefault(category, Metrics())
        for metric in (
            totals,
            item["all"],
            section,
            categories.setdefault(category, Metrics()),
        ):
            metric.add(row)
        if row.get("source_revision"):
            snapshots.add(row["source_revision"])
        if row.get("assessed_at"):
            assessed_dates.add(row["assessed_at"])
    meta = {
        **meta,
        "source_revisions": sorted(
            snapshots
            or ({meta["source_revision"]} if meta.get("source_revision") else set())
        ),
        "assessment_first_at": min(assessed_dates, default=None),
        "assessment_last_at": max(assessed_dates, default=None),
    }
    ranked = []
    for file, item in entries.items():
        ranked.append(
            {
                "file": file,
                "disease": item["disease"],
                "metrics": item["all"].dump(),
                "categories": {
                    c: m.dump() for c, m in sorted(item["categories"].items())
                },
                "source_url": source_url(file, meta.get("source_revision")),
                "history_url": "https://github.com/monarch-initiative/dismech-evals/blob/main/analysis/classification/jev/"
                + quote(Path(file).stem, safe="")
                + "/evidence_claim_match.yaml",
            }
        )
    ranked.sort(key=rank_key)
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
    queues = {}
    for category in [None, *sorted(categories)]:
        candidates = [
            dict(e, metrics=e["categories"][category]) if category else dict(e)
            for e in ranked
            if not category or category in e["categories"]
        ]
        candidates = [e for e in candidates if e["metrics"]["flagged_pairs"]]
        candidates.sort(key=rank_key)
        queues[category or "overall"] = candidates[:top_n]
    selected = {(name, e["file"]): [] for name, queue in queues.items() for e in queue}
    for row in rows():
        if row["status"] != "assessed" or not any(
            a["label"] != "MATCH" for a in row["result"]["answers"].values()
        ):
            continue
        for category in ("overall", row.get("section") or "unclassified"):
            key = (category, row["file"])
            if key in selected:
                selected[key].append(finding(row, meta))
                selected[key].sort(key=finding_key)
                del selected[key][max_findings:]
    packets = {}
    for category, queue in queues.items():
        packets[category] = []
        for rank, entry in enumerate(queue, 1):
            findings = selected[category, entry["file"]]
            packet = {
                "schema_version": VERSION,
                "task": "review_claim_evidence_match",
                "queue": category,
                "rank": rank,
                "file": entry["file"],
                "disease": entry["disease"],
                "metrics": entry["metrics"],
                "provenance": meta,
                "source_url": entry["source_url"],
                "history_url": entry["history_url"],
                "findings": findings,
                "findings_total": entry["metrics"]["flagged_pairs"],
                "findings_truncated": entry["metrics"]["flagged_pairs"] > len(findings),
                "instructions": GUIDANCE,
            }
            packets[category].append(packet)
        filename = (
            "top.jsonl" if category == "overall" else f"categories/{category}.jsonl"
        )
        # Category names originate in schema paths but still must be safe file names.
        if category != "overall" and (not category.replace("_", "").isalnum()):
            raise ValueError(f"Invalid section name: {category}")
        write_jsonl(output / filename, packets[category])
    known_files = {f"{c}.jsonl" for c in categories}
    for stale in (output / "categories").glob("*.jsonl"):
        if stale.name not in known_files:
            stale.unlink()
    dashboard = {
        "schema_version": VERSION,
        "provenance": meta,
        "ranking": RANKING,
        "top_n": top_n,
        "max_findings": max_findings,
        "totals": totals.dump(),
        "entries": ranked,
        "categories": {c: m.dump() for c, m in sorted(categories.items())},
        "queues": {c: [e["file"] for e in queue] for c, queue in queues.items()},
    }
    write_json(output / "dashboard.json", dashboard)
    columns = [
        "rank",
        "file",
        "disease",
        "category",
        "assessed_assertions",
        "mismatch_assertions",
        "partial_assertions",
        "mismatch_fraction",
        "assessed_pairs",
    ]
    with (output / "rankings.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for category in ["overall", *sorted(categories)]:
            candidates = [
                dict(e, metrics=e["categories"][category])
                if category != "overall"
                else e
                for e in ranked
                if category == "overall" or category in e["categories"]
            ]
            for rank, entry in enumerate(sorted(candidates, key=rank_key), 1):
                writer.writerow(
                    entry | entry["metrics"] | {"rank": rank, "category": category}
                )
    schema = Path(__file__).resolve().parents[2] / "schemas/recuration.schema.json"
    (output / "schema.json").write_bytes(schema.read_bytes())
    files = [
        "schema.json",
        "dashboard.json",
        "rankings.csv",
        "top.jsonl",
        *[f"categories/{c}.jsonl" for c in sorted(categories)],
    ]
    manifest = {
        "schema_version": VERSION,
        "provenance": meta,
        "ranking": RANKING,
        "top_n": top_n,
        "max_findings": max_findings,
        "queues": {
            c: {
                "entries": len(v),
                "path": "top.jsonl" if c == "overall" else f"categories/{c}.jsonl",
            }
            for c, v in packets.items()
        },
        "files": {
            f: {
                "sha256": hashlib.sha256((output / f).read_bytes()).hexdigest(),
                "bytes": (output / f).stat().st_size,
            }
            for f in files
        },
    }
    write_json(output / "manifest.json", manifest)
    return dashboard, packets
