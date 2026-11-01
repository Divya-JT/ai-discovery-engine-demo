# AI Discovery Engine — graduation demo deployment package

This is a **separate, sanitized deployment copy**. It does not include the live research database, Run 1 signed artifacts, annotations, results, AI drafts, governance records, backups, tests, or research exports.

## What is included

- `streamlit_app.py` — the verified Streamlit entry point.
- `config.py` — deployment copy; the default DB path is `data/demo_discovery_engine.db`.
- `database/` — required SQLite schema, migrations and repository modules.
- `ingestion/` — file profiling, mapping, validation, duplicate guard and sandbox ingestion.
- `.streamlit/config.toml` — headless server theme/config.
- `requirements.txt` — verified runtime dependencies.
- `runtime.txt` — Python 3.12.
- `data/demo_discovery_engine.db` — sanitized demonstration database.
- `data/app/`, `data/uploads/`, `data/exports/` — empty runtime directories for the upload sandbox and exports.

## Demo data

The demo DB contains **456 public evidence records** and **33 sources**:

| Source | Records |
|---|---:|
| YouTube comments | 309 |
| Google Play reviews | 122 |
| Reddit posts/comments | 24 |
| Website/Forum | 1 |
| **Total** | **456** |

Sanitization applied:

- authors replaced with `anonymous`;
- `source_metadata_json` cleared to `{}`;
- duplicate rows omitted; demo records are unique;
- all screening, annotation, provenance, result, AI-draft, review, freeze, event, rejected-row and audit tables are empty;
- emails, phone numbers, handles, personal-profile links and username-heavy forum thread text excluded;
- no private survey responses or contact details;
- no `.env`, credentials, API keys, live DB, signed artifacts or research governance records.

The full research corpus has 502 records; the demo is a safer, smaller subset for public evaluator access.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open the URL Streamlit prints (normally `http://localhost:8501`).

## Streamlit Community Cloud

1. Create a **private** GitHub repository and push this package (or upload it through the GitHub UI).
2. In Streamlit Community Cloud, create an app from that repository.
3. Select `streamlit_app.py` as the entry point.
4. Select Python 3.12.
5. Use the repository `requirements.txt`.
6. No LLM/API key is required. Do not add a `.env` or secrets.
7. Deploy. The app reads `data/demo_discovery_engine.db` read-only.
8. After testing, share the deployed public app URL with evaluators and keep the repository private.

## Evaluator walkthrough

- **Overview** — corpus counts and source mix.
- **Upload & Processing** — upload a small CSV/XLSX into the isolated sandbox; the live demo DB is untouched.
- **Evidence Explorer** — filter and inspect evidence with IDs, source links, duplicate status.
- **Theme Discovery** — deterministic keyword theme counts and examples.
- **Opportunity Discovery** — evidence-backed opportunity hypotheses (minimum 3 records).
- **Research Q&A** — extractive answers with evidence citations.
- **Research Limitations** — non-representative corpus and engine limitations.
- **Export Findings** — CSV downloads of evidence/themes/opportunities.

## Important limitations

The app is a deterministic discovery/retrieval interface. It does **not** use embeddings, semantic vector retrieval, or an LLM for its answer generation. Theme and opportunity output is exploratory, not validated research. The AI label draft in the separate evidence log was an offline ChatGPT artifact, not an app feature.

The demo DB is a public subset, not the full research corpus. It must not be used for prevalence, market-size, causal or population claims.

## Demo database integrity

- Evidence records: 456; sources: 33
- SQLite journal mode: `DELETE` (single self-contained file; no WAL sidecars)
- SHA-256: `2c55f287cdc1fbdaf1eb6f3e6e6a34b92161be58dbe925725127f21ba4f214e6`
- Only `schema_migrations`, `sources` and `evidence` contain rows; all screening, annotation, result, AI, review, freeze, event, rejected-row and audit tables are empty.

