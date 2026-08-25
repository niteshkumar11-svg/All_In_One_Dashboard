# AGENTS.md

Guidance for AI agents working in this repository.

## Project overview

Single Python **Streamlit** app (`app.py`) — an Ops KPI / BJOC "All In One" Dashboard that reads daily metrics from a Google Sheet via the Sheets REST API. Supporting modules: `data_loader.py` (parsing + API), `build_snapshot.py` (offline CSV builder), `verify_dates.py` (date QA against live sheet).

## Cursor Cloud specific instructions

### Services

| Service | Port | Start command |
|---------|------|---------------|
| Streamlit dashboard | 8501 | `streamlit run app.py --server.headless true --server.port 8501` |

There is no database, Redis, Docker Compose, or separate backend. The only runtime service is Streamlit.

### Dependencies

Python 3.11+ with pip. Install with:

```bash
pip3 install -r requirements.txt
```

`streamlit` installs to `~/.local/bin` on this VM — ensure `export PATH="$HOME/.local/bin:$PATH"` (or use `python3 -m streamlit`) before running commands.

### Credentials (required for full dashboard)

The app **requires** `.streamlit/secrets.toml` with a `[gcp_service_account]` block (see `.streamlit/secrets.toml.example`). Without it, `app.py` shows an error and calls `st.stop()`.

Setup steps:
1. Copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`
2. Paste GCP service-account JSON fields into the `[gcp_service_account]` section
3. Share the target Google Sheet with the service account `client_email` (Viewer is enough)
4. Restart Streamlit

The spreadsheet ID is hardcoded in `data_loader.py` (`SPREADSHEET_ID`). Google Sheets API and Drive API must be enabled in the GCP project.

### Tests and lint

- **Unit self-check:** `python3 test_grid_window.py` (no network, no credentials)
- **Syntax check:** `python3 -m py_compile app.py data_loader.py`
- **No linter** is configured in this repo (no ruff/flake8/pyproject.toml)

### Devcontainer

`.devcontainer/devcontainer.json` uses Python 3.11 image and auto-starts Streamlit on attach via `postAttachCommand`. Equivalent manual start is documented above.

### Gotchas

- README mentions bundled `data/snapshot.csv` offline mode, but **current `app.py` only supports live Google Sheets** — `load_snapshot()` exists in `data_loader.py` but is not wired into the UI.
- `data/snapshot.csv` is gitignored and not present in the repo.
- Streamlit caches sheet data for 5 minutes (`@st.cache_data(ttl=300)`).
- Auto-refresh defaults to 1 minute in the sidebar (`streamlit-autorefresh`).
