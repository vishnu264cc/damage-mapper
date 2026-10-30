# Damage Mapper QA and deployment report

**Run date:** 2026-10-31  
**Scope:** FastAPI application, SQLite persistence and migrations, role-gated workflows, AI adapter behavior, analytics, static frontend, local browser session, and Render/Supabase deployment configuration.

## Result

| Area | Result | Evidence |
|---|---|---|
| Unit, API integration, and migration tests | PASS | `python -m pytest -q`: 60 passed; one upstream Starlette/AnyIO deprecation warning |
| Dependencies and Python configuration | PASS | `pip check`; dotenv import; invalid storage, CORS, host, production database, blank/non-finite/zero settings rejected |
| Fresh database startup | PASS | Alembic `upgrade head` and `/api/health` against an isolated SQLite database |
| Render production configuration | PASS (static/config validation) | Blueprint YAML parsed; platform port binding, pre-deploy migration, host, and `/healthz` settings checked; no live Render deployment was made |
| Supabase storage adapter | PASS (mocked API) | Upload, private-object read, temporary materialization/cleanup, and delete use the configured Supabase Storage API; no live Supabase credentials or project were used |
| CORS, trusted hosts and production guards | PASS (configuration tests) | Exact HTTPS origins and host allowlists validated; production rejects SQLite, local storage, demo seeding, and missing trusted hosts |
| Health check | PASS | `/healthz` returned healthy against the test database and returned 503 when database access was simulated as unavailable |
| Citizen reporting and private evidence | PASS | Browser signup, PNG upload, successful report creation, and own-report queue; API test checks evidence authorization |
| AI classification and fallback | PARTIAL | CLIP/YOLO adapter behavior and unavailable/model-disabled fallback covered with fake adapters and API tests; pretrained model weights were not downloaded or evaluated |
| Severity, confidence separation, and priority | PASS (logic) | Unit tests assert bounded decision-support scores, evidence, uncertainty, and that model confidence is not used as severity |
| Duplicate detection | PASS (test data) | Same image and nearby coordinates produced a review candidate; neither report was removed |
| Map and location handling | PASS (local browser) | Live API returned three demo incidents and the Leaflet map showed a three-item marker cluster; map rendering had no uncaught page errors |
| Admin and responder workflow | PASS (integration) | Admin verification and assignment, responder login and status updates through resolution |
| Analytics and CSV | PASS | Date/location filters, grouped metrics, review duration, matching CSV records, authorization, and formula-like text escaping |
| Mobile layout | PASS (local browser) | Citizen reporting and map were checked at a narrow viewport; document width did not exceed the measured viewport |
| PostgreSQL/PostGIS | NOT RUN | No PostgreSQL/PostGIS service was available; all executed database tests used SQLite |

## Reproduced fixes

1. Local `.env` files were not read even though configuration was environment-based. Added `python-dotenv` loading from the project root without overriding deployment-provided environment values, and added `.env.example`.
2. Non-finite duplicate radius/window and priority-threshold values could pass configuration range checks. Settings now reject NaN/infinity and invalid upload limits at startup; regression tests cover these inputs.
3. The browser requested a missing `/favicon.ico` and received 404. Added an empty data favicon to avoid the broken request.
4. Render's ephemeral filesystem was not suitable for incident evidence. Added a private Supabase Storage adapter while retaining local uploads for development; the adapter was tested with a mocked HTTP client.
5. Added production configuration guards, exact-origin CORS and trusted-host middleware, a database-aware `/healthz` endpoint, Render Blueprint configuration, and a Python version pin. Moved heavyweight inference dependencies to optional requirements so the model-disabled web service does not install Torch/Transformers by default.

## Automated coverage

`tests/test_end_to_end.py` exercises the HTTP workflow from citizen registration and evidence submission through duplicate review, administrator verification/assignment, responder status updates, database-backed analytics, filtered CSV export, and citizen report visibility. Other tests cover migration from legacy SQLite data, local and mocked Supabase upload handling, upload validation, CLIP/YOLO adapter contracts, model-unavailable fallback, severity/priority logic, geographic queries, production configuration, authentication, rate limiting, and endpoint authorization.

## Security and deployment caveats

- Role and ownership checks, revocable sessions, upload authorization, rate limits, and analytics/export access are exercised by API tests. This run did **not** include an external penetration test, dependency vulnerability scan, or production reverse-proxy review; test results are not a security certification.
- Do not deploy the example's SQLite/demo configuration. Use PostgreSQL/PostGIS, HTTPS, durable private evidence storage, a secrets manager, `DM_ENABLE_DEMO=0`, backups, and a one-time administrator bootstrap followed by removal of the bootstrap secret.
- Real CLIP and YOLO weight loading, GPU/CPU capacity, model accuracy, and inference latency remain deployment-specific and unverified. Inference is lazy and loads per process.
- Render Blueprint syntax and settings were validated locally, but no Render service was created or deployed. Supabase Storage operations were tested against a mock only; Supabase database connectivity, PostGIS permissions, and production migrations remain unverified.
- OpenStreetMap tiles, Leaflet CDN assets, and Photon search require outbound network access. The map rendered with live incident markers in this test, but external tile/search availability is not guaranteed.
