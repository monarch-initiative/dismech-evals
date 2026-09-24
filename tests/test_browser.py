"""Real browser checks for navigation, exact evidence, downloads and mobile layout."""

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from dismech_evals.products import build_products, history_rows, metadata
from dismech_evals.site import build_site

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("browser")
    products = root / "products"
    build_products(
        lambda: history_rows(ROOT / "analysis/classification/jev", "jev-1.13.0"),
        metadata({"model": "jev-1.13.0"}, mode="saved_history"),
        products,
    )
    output = root / "site"
    build_site(products, output)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(output))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/", output
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield page
    page.close()
    assert not errors


def test_navigation_search_category_and_back(page, site):
    url, _ = site
    page.goto(url + "entries.html")
    assert page.locator("tbody tr").count() == 2
    page.locator("#search").fill("Asthma")
    assert page.locator("tbody tr").count() == 1
    page.locator("#category").select_option("phenotypes")
    assert "category=phenotypes" in page.url
    page.locator("tbody a").first.click()
    page.wait_for_selector("#findings section")
    assert page.locator("h1").inner_text() == "Asthma"
    assert "/phenotypes/" in page.locator("#findings").inner_text()
    assert page.locator("blockquote").count() > 0
    assert page.locator("#findings table code").first.inner_text() == "/"
    page.get_by_text("Complete evaluated claim", exact=True).first.click()
    assert '"about"' in page.locator("#findings pre").first.inner_text()
    page.go_back()
    assert page.locator("#category").input_value() == "phenotypes"
    assert page.locator("#search").input_value() == "Asthma"
    page.locator("#search").fill("No such disease")
    assert "No entries match" in page.locator("#content").inner_text()


def test_download_matches_schema_and_category(page, site):
    url, _ = site
    page.goto(url + "agents.html")
    with page.expect_download() as info:
        page.get_by_role("link", name="Download overall JSONL").click()
    rows = [
        json.loads(line) for line in Path(info.value.path()).read_text().splitlines()
    ]
    assert rows and all(r["queue"] == "overall" for r in rows)
    with page.expect_download() as info:
        page.locator('a[href="downloads/categories/phenotypes.jsonl"]').click()
    rows = [
        json.loads(line) for line in Path(info.value.path()).read_text().splitlines()
    ]
    assert all(f["section"] == "phenotypes" for r in rows for f in r["findings"])
    assert page.request.get(url + "downloads/schema.json").ok


def test_desktop_mobile_file_urls_and_no_remote_requests(page, site):
    url, directory = site
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    for base in (url, directory.as_uri() + "/"):
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for view in ("index", "entries", "categories", "agents", "method"):
                page.goto(base + view + ".html")
                assert page.locator("#content section, #content .category-grid").count()
                assert page.locator('nav [aria-current="page"]').count() == 1
                assert not page.evaluate(
                    "document.documentElement.scrollWidth > innerWidth"
                ), (view, width)
                assert (
                    "Limited saved-history snapshot"
                    in page.locator("#scope").inner_text()
                )
    assert all(r.startswith((url, "file:", "data:")) for r in requests)


def test_direct_entry_link_loads_packet_from_file(page, site):
    _, directory = site
    page.goto(
        directory.as_uri()
        + "/entry.html?file=kb%2Fdisorders%2FAsthma.yaml&category=phenotypes"
    )
    page.wait_for_selector("#findings section")
    assert page.locator("h1").inner_text() == "Asthma"
    page.reload()
    page.wait_for_selector("#findings table")
    assert "/about/disease" in page.locator("#findings").inner_text()
