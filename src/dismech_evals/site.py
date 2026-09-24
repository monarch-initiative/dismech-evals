"""Build a self-contained static dashboard from a recuration product."""

import hashlib
import json
import shutil
from pathlib import Path

from dismech_evals.products import jsonl

ASSETS = Path(__file__).parent / "web"
PAGES = {
    "index": "Evidence in focus",
    "entries": "Entry rankings",
    "categories": "Claim categories",
    "agents": "Agent work queues",
    "method": "Reading the results",
    "entry": "Entry review",
}


def script_json(value):
    # Prevent HTML/script injection even when a quoted snippet contains markup.
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_site(products, output):
    data = json.loads((products / "dashboard.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    downloads = output / "downloads"
    if downloads.exists():
        shutil.rmtree(downloads)
    shutil.copytree(products, downloads)
    detail_dir = output / "packets"
    if detail_dir.exists():
        shutil.rmtree(detail_dir)
    detail_dir.mkdir()
    per_entry = {}
    for path in [
        products / "top.jsonl",
        *sorted((products / "categories").glob("*.jsonl")),
    ]:
        for packet in jsonl(path):
            per_entry.setdefault(packet["file"], {})[packet["queue"]] = packet
    for entry in data["entries"]:
        packets = per_entry.get(entry["file"])
        if packets:
            filename = hashlib.sha256(entry["file"].encode()).hexdigest()[:20] + ".js"
            entry["detail"] = "packets/" + filename
            (detail_dir / filename).write_text(
                "window.ENTRY_PACKETS=" + script_json(packets) + ";\n"
            )
    (output / "data.js").write_text("window.EVAL_DATA=" + script_json(data) + ";\n")
    for name in ("site.css", "site.js"):
        shutil.copyfile(ASSETS / name, output / name)
    template = (ASSETS / "page.html").read_text()
    for key, title in PAGES.items():
        (output / f"{key}.html").write_text(
            template.replace("__PAGE__", key).replace("__TITLE__", title)
        )
    (output / ".nojekyll").write_text("")
