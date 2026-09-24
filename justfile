# Resolve and install the configured classifier and source revisions.
[positional-arguments]
setup *args:
    uv run dismech-evals setup "$@"

# Pass dismech options such as --section, --limit, --workers or --dry-run.
[positional-arguments]
audit *args:
    uv run dismech-evals audit "$@"

[positional-arguments]
inventory *args:
    uv run dismech-evals audit --dry-run "$@"

[positional-arguments]
cache *args:
    uv run dismech-evals cache "$@"

[positional-arguments]
report *args:
    uv run dismech-evals report "$@"

check:
    uv run ruff check .
    uv run pytest -q

# Build deterministic agent work queues from saved predictions.
[positional-arguments]
products *args:
    uv run dismech-evals products "$@"

# Render the static dashboard and downloadable work queues.
[positional-arguments]
site *args:
    uv run dismech-evals site "$@"

# Preview the generated site locally.
serve port="8000": site
    uv run python -m http.server {{port}} --directory build/site --bind 127.0.0.1

# One-time browser setup, then test the rendered site.
setup-browser:
    uv sync --group browser
    uv run --group browser playwright install chromium

test-browser: site
    uv run --group browser pytest tests/test_browser.py -q
