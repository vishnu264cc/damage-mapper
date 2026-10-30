# Damage Mapper

AI-assisted disaster damage assessment and response prioritization. This prototype provides a responsive operations dashboard and a documented FastAPI service; AI assessments and priority scores require human review.

## Backend architecture

```text
app/
  api/routes/       Health and incident endpoints
  core/             Environment-driven configuration
  db/               SQLAlchemy base, engine and session dependency
  models/           Users, incidents, images, assignments, history and audit
  schemas/           Pydantic request and response contracts
  services/ai/       CLIP/YOLO adapters, image preprocessing and assessment
  services/prioritization/  Preliminary severity, priority and duplicate-review logic
  services/          Incident workflows, scoring and validated uploads
  utils/             Geographic distance and query helpers
  main.py            FastAPI application and exception handlers
migrations/          Alembic environment and schema revisions
tests/               SQLite-backed API and upload tests
static/              Existing single-page frontend
```

## Local development (SQLite)

Python 3.10+ is supported. From this directory:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
# Set DM_ADMIN_BOOTSTRAP_SECRET in .env to a unique random value of at least 32 characters.
alembic upgrade head
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. `.env` is loaded from the project root without overriding variables already set by the process. The SQLite database defaults to `damage_mapper.db` in this directory. The example disables model downloads for deterministic, offline local work. To enable CLIP locally, install `requirements-ai.txt` and set `DM_DISABLE_MODEL=0`; this may download large model weights. The initial Alembic revision imports existing legacy incident records and keeps the original table as `incidents_legacy` for recovery. Back up the database before applying migrations.

The regular model mode loads `openai/clip-vit-base-patch32` lazily and may download model weights on first inference. Torch/Transformers installation and inference can require substantial disk, memory, and startup time.

## Image assessment engine

The adapter-based engine lives in `app/services/ai/`: image validation and EXIF-aware RGB/resize preprocessing are shared across adapters; a lazy Hugging Face CLIP zero-shot classifier returns Flood damage, Building damage, Fire and smoke, Landslide, Cyclone damage, Road and infrastructure obstruction, or Other / unclear; and an optional Ultralytics YOLO adapter returns actual detected class names, confidence scores, and normalized bounding boxes. Routes call the engine, not a model vendor. The standalone `POST /api/ai/analyze` endpoint accepts the same validated multipart image and returns `status`, nullable `predicted_category` and `confidence`, `model_name`, `processing_time_ms`, the top three `candidate_classes`, `detected_objects`, detector availability, and `review_required`. The same structured result is saved with each report and included as `ai_analysis`; existing incident response fields remain available.

`DM_CLIP_MODEL` selects any Hugging Face model compatible with the Transformers `zero-shot-image-classification` CLIP pipeline. Fine-tuned CLIP checkpoints must retain the compatible model/configuration and image/text processor; prompts are mapped to the platform's fixed category names. Object detection is opt-in: install it with `pip install -r requirements-ai-yolo.txt` and set `DM_YOLO_MODEL` to a supported pretrained model ID or local fine-tuned `.pt` weights. If either adapter cannot load or infer, its status is explicit; a missing classifier yields no predicted category, no confidence, and a required human review rather than a guessed result. `DM_DISABLE_MODEL=1` disables inference and is useful for offline development/tests.

Set `DM_AI_REVIEW_THRESHOLD` to a value from `0` to `1` (default `0.55`). A result below this threshold is flagged for review; the threshold is a workflow setting, not a validated safety boundary. CLIP scores are uncalibrated ranking scores and must not be interpreted as probabilities, damage severity, verification, or evidence of structural safety. Severity and priority are calculated independently from submitted urgency, reported people affected, and blocked access; they remain preliminary human-review heuristics.

### Fine-tuning data and evaluation

Use appropriately licensed, consented disaster imagery with provenance and privacy review. Define clear, mutually understood labels for all seven categories, including genuinely ambiguous/no-visible-damage examples; have qualified reviewers label edge cases and retain annotator disagreement. Split train/validation/test data by disaster event and geography (not random near-duplicate images) to prevent leakage. Track source, license/consent, capture context, label version, class balance, and known collection biases. Keep a held-out test set untouched during training and prompt/model selection. Measure per-class precision/recall, macro-F1, confusion matrices, and confidence calibration across regions, image quality, and event types; choose the review threshold based on operational risk and validate it with responders before use.

For YOLO, additionally provide bounding-box annotations and a documented class map for each visible object/hazard, review box quality, and evaluate per-class precision/recall and mAP on an event/geography-separated test set. Generic pretrained COCO weights can detect only their trained object classes (for example, people or vehicles); they do not reliably recognize flood depth, structural integrity, fire severity, or other disaster-specific hazards. A fine-tuned model's weights, dataset manifest, label map, training configuration, metrics, and model license should be versioned and reviewed before setting `DM_YOLO_MODEL`. Neither model replaces field verification or emergency assessment.

## PostgreSQL and PostGIS

Install and enable PostgreSQL with the PostGIS extension. Point the app and Alembic at the same database URL, then migrate:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://db-user:replace-me@localhost:5432/damage_mapper"
alembic upgrade head
uvicorn app.main:app
```

The migration enables PostGIS and creates a GiST geography expression index for the 150 m duplicate-candidate search. The SQLite development path uses the same incident records and a Haversine distance fallback. Keep credentials in environment/secrets management; do not commit them.

## Database migrations

Always point `DATABASE_URL` at the intended database before running Alembic; the migration command and web process must use the same URL. On an existing installation, take and verify a database backup first. Then, from the project root:

```powershell
Copy-Item .env.example .env  # only for a new local setup
# Edit .env and set DATABASE_URL
alembic upgrade head
alembic current
```

The initial migration imports the legacy SQLite schema where present; later revisions add normalized tables and authentication/geospatial structures. PostgreSQL deployments must install PostGIS and allow the configured database user to create the extension before the first migration. Do not edit the Alembic version table manually. Review the revision files and rehearse migrations against a recent database copy before production. Run migrations once as a release step, not concurrently in every application worker.

## API and schemas

FastAPI publishes the OpenAPI specification at `/openapi.json` and Swagger UI at `/docs`.

| Method | Path | Purpose | Response |
|---|---|---|---|
| `GET` | `/api/health` | Database and model service status | `HealthResponse` |
| `POST` | `/api/auth/register` | Register a citizen account and issue a revocable bearer session | `AuthTokenRead` |
| `POST` | `/api/auth/login` | Verify credentials and issue a revocable bearer session | `AuthTokenRead` |
| `GET` | `/api/auth/me` | Read the authenticated account and role | `UserRead` |
| `POST` | `/api/auth/logout` | Revoke the current bearer session | `204 No Content` |
| `POST` | `/api/auth/bootstrap-admin` | Create the first administrator using a one-time deployment secret | `AuthTokenRead` |
| `GET` | `/api/auth/users` | List active accounts for assignment/provisioning (admin) | `list[UserRead]` |
| `POST` | `/api/auth/users` | Provision responder or administrator accounts (admin) | `UserRead` |
| `PATCH` | `/api/auth/users/{user_id}` | Change account role, password or active status (admin) | `UserRead` |
| `POST` | `/api/ai/analyze` | Assess an uploaded image using configured model adapters | `AIAnalysisRead` |
| `GET` | `/api/stats` | Summary card totals | `IncidentStatsResponse` (existing fields retained) |
| `GET` | `/api/analytics` | Filtered database counts, trends, geography and verification timing (responder/admin) | `IncidentAnalyticsResponse` |
| `GET` | `/api/analytics/export.csv` | Stream matching incident records as spreadsheet-safe CSV (responder/admin) | `text/csv` |
| `GET` | `/api/incidents` | Search, filter, sort and page through reports | `IncidentPage` |
| `POST` | `/api/incidents` | Create report with validated multipart image and preliminary assessment | `IncidentRead` |
| `GET` | `/api/incidents/map` | Bounded map-point query, optionally filtered by severity | `IncidentMapResponse` |
| `GET` | `/api/responders/locations` | Read explicitly stored responder GPS locations (admin) | `ResponderLocationList` |
| `PUT` | `/api/responders/locations` | Update own location (responder) or a location (admin) | `ResponderLocationRead` |
| `GET` | `/api/incidents/{id}` | Retrieve a report | `IncidentRead` |
| `GET` | `/api/incidents/{id}/image` | Retrieve private image evidence for an authorized user | Image bytes |
| `PATCH` | `/api/incidents/{id}` | Verify/reject or assign (admin); update assigned response status (responder) | `IncidentRead` |
| `DELETE` | `/api/incidents/{id}` | Delete report and unshared image files (admin) | `204 No Content` |
| `GET` | `/api/incidents/{id}/history` | Verification and audit history (responder/admin) | `IncidentHistoryResponse` |
| `GET` | `/api/incidents/{id}/assessment` | Refresh and retrieve preliminary severity, priority, and duplicate assessment | `IncidentDecisionAssessmentRead` |
| `POST` | `/api/incidents/{id}/duplicates/analyze` | Find nearby, image-similar candidate reports for human review | `DuplicateCandidatesRead` |
| `GET` | `/api/duplicates` | List candidate pairs, optionally filtered by review status | `DuplicateCandidatesRead` |
| `PATCH` | `/api/duplicates/{candidate_id}` | Confirm/reject a candidate without deleting reports | `DuplicateReviewRead` |
| `GET` | `/api/prioritization/weights` | Read current priority weights and high-risk threshold | `PriorityWeightsRead` |
| `PUT` | `/api/prioritization/weights` | Update weights and recompute incident priority | `PriorityWeightsRead` |
| `POST` | `/api/demo-seed` | Insert stable, synthetic demo incidents | `DemoSeedResponse` |
| `GET` | `/api/demo-seed` | Backward-compatible demo endpoint | `DemoSeedResponse` (deprecated) |

`GET /api/incidents` accepts `page` (1-based), `page_size` (1–100), `q`, `status` (legacy alias) or `verification_status`, `incident_status`, `severity`, `damage_type`, `sort_by`, and `sort_order`. Its response is `{items, total, page, page_size, pages}`. The frontend consumes this page envelope; its search and filters are sent to the server. Global dashboards use `/api/stats` and `/api/analytics`, not the current page’s counts.

`GET /api/analytics` and `GET /api/analytics/export.csv` share optional `date_from`, `date_to` (inclusive ISO dates), `min_latitude`, `max_latitude`, `min_longitude`, `max_longitude`, `damage_type`, `severity`, `verification_status`, and `incident_status` filters. Geographic bounds are inclusive and must be provided as a complete rectangle. Counts, category/status breakdowns, trends, and 0.01-degree coordinate-cell concentrations are aggregated from matching database incidents. Trends are daily for date windows up to 90 days and monthly otherwise. Average verification time uses each incident's first `Verified` or `Rejected` history entry; reports never reviewed have no duration. The high-priority count uses `DM_HIGH_RISK_PRIORITY_THRESHOLD` (default 75). CSV export streams only matching reports and prefixes formula-like text cells to reduce spreadsheet formula injection risk. Operational analytics and exports require a responder or administrator session.

The map endpoint returns live, valid-coordinate incidents with priority, category, severity, report time, verification/response state, assignment and duplicate-group references, plus a `possible_duplicate` flag for un-rejected candidate pairs. It accepts `severity`, `damage_type`, `limit` (1–5000), and `offset`; the frontend retrieves all pages rather than silently truncating large incident sets. Geographic filtering requires `latitude`, `longitude`, and `radius_m` together (0–500,000 m, exclusive lower bound); PostgreSQL uses PostGIS `ST_DWithin`, while SQLite applies a bounding-box prefilter followed by Haversine distance. `invalid_coordinates` counts records excluded for invalid GPS data. The map uses Leaflet with OpenStreetMap/HOT basemaps, live backend markers, optional clustering and priority heatmap overlays, duplicate filtering, and Photon/OSM place search. Search results only recenter the map; incident data and all incident filters still come from the API.

Responder GPS is optional and stored separately from incident reports. Responder location reads and writes require an authenticated administrator; a responder may update only their own location. `PUT /api/responders/locations` accepts `{responder_name, latitude, longitude}` and upserts that responder's latest location. Each map popup shows its update time. Responder locations are sensitive operational data and should only be used over HTTPS.

The incident creation multipart request retains the existing frontend field names: required `image`, `description`, `latitude`, and `longitude`; optional `urgency`, `people_affected`, `access_blocked`, and legacy `reporter` (the authenticated account name is always used). Uploads require authentication, are rate limited, default to 10 MiB, accept JPEG/PNG/WEBP, validate actual file content and image dimensions, are re-encoded to remove embedded metadata, and are stored using generated names. Uploaded files are not publicly mounted; both the new incident-image endpoint and the legacy `/uploads/{name}` URL require authorization. Configure `DM_MAX_UPLOAD_BYTES` and `DM_MAX_IMAGE_PIXELS` to change the limits.

Incident response fields used by the existing frontend (including `image_url`, `model_confidence`, `verification_status`, `incident_status`, and `assigned_to`) remain present. The list response intentionally adds a pagination envelope; clients that previously expected a bare array should read its `items` field.

## Preliminary prioritization and duplicate review

Every new incident includes a `decision_assessment` marked as preliminary decision support. Severity is derived from reported urgency, a logarithmic population-exposure indicator, access blockage, and sufficiently confident YOLO hazard detections when available. CLIP category and confidence are deliberately excluded from severity. Evidence and uncertainty are returned with the assessment; model detections are unverified scene indicators and never proof of severity or structural safety.

Priority is a transparent weighted sum on a 0-100 scale. Defaults are severity 0.40, urgency 0.25, exposure 0.20, and accessibility 0.15. Exposure uses a log scale capped at 5,000 reported people. Each factor returns its normalized input, configured weight, score contribution, and explanation. A Critical severity or score at/above `DM_HIGH_RISK_PRIORITY_THRESHOLD` (default 75) requires high-risk human review, regardless of model confidence. All assessment outputs are preliminary, not emergency-service instructions or professional damage assessments.

Duplicate candidates require a perceptual-hash match and both configurable geographic and temporal proximity checks. The score combines image similarity (0.50), distance (0.30), and time (0.20); defaults are 150 m, 48 hours, and maximum hash distance 10, configurable with `DM_DUPLICATE_RADIUS_M`, `DM_DUPLICATE_WINDOW_HOURS`, and `DM_DUPLICATE_HASH_DISTANCE`. Candidate pairs are grouped and retained for review; confirming or rejecting them never deletes or merges incident reports.

Priority weights must sum to 1. Only authenticated administrators can update weights or adjudicate duplicate candidates; responders can read assessments and candidate groups.

## Authentication and authorization

Passwords are hashed with Argon2id. Public signup creates only `citizen` accounts; clients cannot choose elevated roles. Access tokens are cryptographically random opaque bearer values, returned once, stored only as SHA-256 hashes, and expire after `DM_AUTH_TOKEN_TTL_MINUTES` (default 480 minutes). Logout revokes the server-side session. The browser keeps the token in per-tab `sessionStorage` and sends it in the `Authorization: Bearer ...` header. Deploy behind HTTPS; never put tokens in URLs or logs.

For initial setup, generate a unique secret of at least 32 characters and configure it through the deployment secret store as `DM_ADMIN_BOOTSTRAP_SECRET`. A configured secret shorter than 32 characters is rejected; if unset, bootstrap stays disabled. Call the bootstrap endpoint once over HTTPS, sourcing both the bootstrap secret and initial password from the deployment secret store:

```powershell
$payload = @{ email = "admin@example.org"; display_name = "Emergency Administrator"; password = $env:DM_BOOTSTRAP_ADMIN_PASSWORD } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "https://damage-mapper.example.org/api/auth/bootstrap-admin" -Headers @{ "X-Admin-Bootstrap-Secret" = $env:DM_ADMIN_BOOTSTRAP_SECRET } -ContentType "application/json" -Body $payload
```

Bootstrap is refused after an administrator exists, and a database claim prevents concurrent bootstrap attempts from creating multiple first admins. Remove the bootstrap secret from the runtime environment after initialization. An administrator provisions responder and additional admin accounts through `/api/auth/users`; existing legacy accounts with no password can be recovered by an admin using `/api/auth/users/{user_id}`. Responders are assigned by account email or unique display name. Roles are `citizen`, `responder`, and `admin` (Emergency administrator). Citizens can create reports and read only their own reports; responders can access operational reports and update response status only for assigned incidents; administrators can verify/reject, assign, adjudicate duplicates, configure prioritization, manage responder locations, and delete records. AI and incident uploads are rate limited using database-backed fixed-window counters for SQLite and PostgreSQL. Limits are configurable with `DM_AUTH_LOGIN_LIMIT`, `DM_AUTH_REGISTER_LIMIT`, and `DM_AUTH_UPLOAD_LIMIT`. Client IPs come from the direct connection; do not trust forwarded IP headers unless a trusted-proxy deployment is explicitly added.

## Demo and tests

1. Start the server and open the dashboard.
2. Select **Load sample incidents** to create clearly labeled synthetic reports.
3. Submit an image with GPS coordinates or enter coordinates manually.
4. Search/filter the queue, verify/reject reports, assign a responder, and update response status.

Demo seeding is enabled by default for local development. Set `DM_ENABLE_DEMO=0` to disable both demo-seed routes outside local development.

The base `requirements.txt` intentionally excludes the large CLIP/Torch inference dependencies. Install `requirements-ai.txt` for CLIP, or `requirements-ai-yolo.txt` for the optional detector.

Run the automated API, validation, audit, upload and SQLite tests:

```powershell
python -m pytest -q
```

The suite includes unit tests for AI adapters/fallback, severity, priority and duplicate scoring; SQLite-backed API integration and role-control tests; migration checks; and an HTTP end-to-end report-to-response workflow in `tests/test_end_to_end.py`. Model tests use fake adapters and the end-to-end workflow runs with AI disabled, so a passing suite does not certify external pretrained weights or model quality.

## Deployment

For Render, use the included [render.yaml](./render.yaml) Blueprint and the production-only [`.env.production.example`](./.env.production.example) as an environment checklist. The service listens on `0.0.0.0:$PORT`, runs Alembic as a pre-deploy release step, and uses `/healthz` for readiness. Local development continues to use SQLite and local uploads from `.env.example`.

### Supabase setup (manual)

1. Create a Supabase project and a private Storage bucket named `incident-evidence`. Set its file-size limit to 10 MiB and, if bucket MIME restrictions are enabled, allow `image/jpeg`, `image/png`, and `image/webp`. Do not make evidence public.
2. In Database > Extensions, enable `postgis` before the first deployment migration. The initial Alembic revision also requests this extension; the database role must be permitted to use it.
3. Copy the PostgreSQL connection string from Supabase. Prefer its Session Pooler connection when the Render network cannot reach the direct database endpoint (commonly due to IPv6-only direct connectivity). Configure it as `DATABASE_URL` in Render; the application accepts `postgresql://` and converts it to the psycopg driver. Use a URL-encoded password if it contains reserved URL characters. Use a connection suitable for Alembic DDL, not a transaction pooler unless you have verified migrations against it.
4. Copy the project URL to `SUPABASE_URL` and the server-side service-role/secret key to `SUPABASE_SERVICE_ROLE_KEY` in Render. The private key is used only by the backend to upload, read, and delete bucket objects. Never put it in `static/`, frontend JavaScript, an `EXPO_PUBLIC_*`/`VITE_*` setting, source control, or support logs.
5. Supabase Storage uses its authenticated object API. The application stores only generated object keys in PostgreSQL, validates and normalizes images before upload, and serves evidence through existing authenticated API endpoints. Temporary processing files are removed after inference. SQLite/local-file mode remains available for development.

### Render setup (manual)

1. Push the project to a private GitHub repository. The repository ignore rules exclude `.env` and other populated `.env.*` files while allowing the two example templates. Verify no real credentials were committed in earlier history; rotate any credential that was.
2. In Render, choose **New > Blueprint**, connect the repository, and review `render.yaml`. The Blueprint provisions one Starter web service. Its `preDeployCommand` runs `alembic upgrade head` before releases; this is intentionally a paid Render service feature. Do not also run migrations concurrently from web workers.
3. Populate the Blueprint's unsynced variables in the Render dashboard: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and a unique `DM_ADMIN_BOOTSTRAP_SECRET` (at least 32 characters). The example service hostname is `damage-mapper-api.onrender.com`; add every custom Render/domain hostname to `DM_TRUSTED_HOSTS` as a comma-separated host list. Same-origin frontend/API use needs no CORS origins. If a separate frontend origin is used, set `DM_CORS_ORIGINS` to its exact HTTPS origin(s), without paths or a wildcard.
4. Confirm Render's build command installs `requirements.txt`, its start command is `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`, and its health-check path is `/healthz`. `PORT` is supplied by Render. Model inference and demo seeding are disabled in this deployment template; the app returns its explicit model-unavailable assessment until a compatible inference host is configured.
5. Deploy and inspect build, pre-deploy migration, and runtime logs. Verify `/healthz` returns `200` with `{"status":"ok","database":"ok"}` and `/api/health` reports the application status. Then bootstrap the first admin once over HTTPS, using the bootstrap secret from Render and an independently generated strong password. Remove `DM_ADMIN_BOOTSTRAP_SECRET` from the service environment after successful bootstrap.
6. For a separate frontend origin, redeploy after setting exact CORS origins and trusted host names. CORS is disabled by default; wildcard origins are not used with credentialed requests.

The Render service intentionally uses one worker and disables large model downloads to fit a modest web process. To run CLIP inference, install `requirements-ai.txt`, provision adequate RAM/disk/CPU (or GPU) and model-cache storage, then set `DM_DISABLE_MODEL=0`; this was not load-tested here. YOLO is separately optional via `requirements-ai-yolo.txt`. Neither hosted inference nor model accuracy is guaranteed by this deployment configuration.

### Render/Supabase troubleshooting

| Symptom | Check |
|---|---|
| Startup rejects production configuration | Set a PostgreSQL `DATABASE_URL`, `DM_STORAGE_BACKEND=supabase`, `DM_ENABLE_DEMO=0`, Supabase URL/key/bucket, and a non-empty `DM_TRUSTED_HOSTS`. The app intentionally refuses unsafe production defaults. |
| Alembic fails before deploy | Confirm the Supabase database URL/password, network reachability from Render, PostGIS is enabled, and the connection role can create/use the application schema and PostGIS objects. Review the pre-deploy log before retrying. |
| `/healthz` returns 503 | The application cannot query the migrated database. Verify `DATABASE_URL`, database availability, and that the pre-deploy migration succeeded. |
| Uploads or evidence reads return 503 | Check `SUPABASE_URL`, the server-only service role key, bucket name, Supabase project status, bucket size/MIME settings, and Storage logs. Keep the bucket private. |
| Render returns 400 for every request | Add the actual request hostname (including any custom domain) to `DM_TRUSTED_HOSTS`. |
| Browser reports a CORS error | Same-origin requests need no CORS setting. For a separate frontend, use the exact scheme/host/port origin in `DM_CORS_ORIGINS`, without a route path, then redeploy. |
| AI classification is unavailable | The Blueprint deliberately sets `DM_DISABLE_MODEL=1`. Keep this safe fallback or provision optional model dependencies and sufficient resources before enabling inference. |

Keep database backups and Supabase Storage retention/backup policies separate and tested. Render's filesystem is ephemeral; it is used only for temporary image processing, never as the durable production evidence store. Leaflet, OpenStreetMap tiles, and location search require outbound network access.

### Environment variables

`.env.example` lists local settings; `.env.production.example` is a secret-free production checklist. Additional deployment settings are `DM_ENVIRONMENT`, `DM_STORAGE_BACKEND`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET`, `DM_CORS_ORIGINS`, and `DM_TRUSTED_HOSTS`. Render's Blueprint leaves database credentials and service secrets unsynced for manual configuration. Never commit populated `.env` files or use local SQLite/file storage in production.

## QA test report

See [QA_TEST_REPORT.md](./QA_TEST_REPORT.md) for the executed checks, precise coverage, and items that could not be verified in this environment.

## Data model and operational limits

SQLAlchemy models cover users, revocable authentication sessions, persistent rate-limit buckets, incidents, incident images, verification history, duplicate groups, responder assignments, and actor-linked audit logs. Incident/report changes are timestamped, and verification and assignment changes are retained as history. This does not replace operational security review, secret rotation, backup, privacy, retention, or incident-response controls.

Historic and synthetic incidents are marked `legacy` or `synthetic` in `ai_analysis` and are not represented as current model predictions; historic compatibility fields are retained for existing clients. CLIP outputs are coarse scene interpretations; the model cannot establish structural integrity or casualty counts. Severity and priority are prototype heuristics and must be validated with disaster-management experts before operational use. Duplicate matching is only a review candidate. Map tiles use Leaflet and OpenStreetMap and require network access.
