# Codecheck — annotation verification

A basic Django 5.2 + Next.js 16 tool for independently coding qualitative posts and verifying AI annotations. The included study has **60 posts and 44 codes**, loaded from columns **E–AV** of `FINAL_DATA_RECODE_RD2_Validate_AI.xlsx`.

## Run locally

Requires Python 3.10+ and Node.js 20.9+.

```bash
./scripts/setup.sh
./scripts/dev.sh
```

Open **http://localhost:3000** and select **Create account**. No default account or shared password is created. Dependencies and the study database have already been initialized in this workspace; `./scripts/dev.sh` starts the existing installation.

Register and sign in with an email address and password. No username is requested or displayed. Email matching is case insensitive, and each email identifies one account. Existing accounts keep their saved annotations and can sign in with their registered email and existing password. There are no password-strength or minimum-length requirements; passwords are stored as hashes and checked when signing in.

The development script runs Django on `127.0.0.1:8000` and Next.js on `127.0.0.1:3000`. Next.js proxies `/api/` to Django, so session cookies and CSRF protection use the same browser origin. Press Ctrl+C to stop both servers. Set `BACKEND_PORT` and `FRONTEND_PORT` if the defaults are occupied; for a different frontend port, set `CSRF_TRUSTED_ORIGINS` to its full browser origin as well.

## Rater workflow

1. Sign in. Progress and annotations belong to that account.
2. Read the post’s title and complete text. Choose any applicable codes from all 44 E–AV headers. The plain checkbox list shows the original spreadsheet headers, grouped by research question, with a code search field. An explicit **No applicable codes** choice handles posts with no matching code.
3. Confirm the independent choices. They become locked, and Django then reveals the codes marked `1` by the AI. The API and personal downloads withhold AI labels for posts whose independent choices have not been confirmed.
4. Accept or reject **each AI-positive code**. Your independent choices remain available for comparison. AI-zero codes do not need a verification decision.
5. Complete verification and continue to the next unfinished post. A rater can revisit and change AI decisions; doing so reopens the post until it is completed again. Independent choices stay locked after exposure to AI labels.

Use the post dropdown or Previous/Next buttons to move between posts. The screen shows completion counts and the current save status.

Code choices, the no-codes choice, confirmation, AI decisions, completion, and notes all save automatically. The interface shows saving / saved / failure states and prevents navigation while a save is pending. In-flight requests are serialized, and stale revisions cannot silently overwrite another tab’s work. Keep the page open if a save fails and use **Retry saving**. Research notes save on each input change.

Each account gets all study posts. This app does not assign different subsets to raters.

## Where the data lives

| File | Purpose |
| --- | --- |
| `FINAL_DATA_RECODE_RD2_Validate_AI.xlsx` | Original input; never modified by the app |
| `backend/data/source.xlsx` | Frozen copy of the imported workbook |
| `backend/data/db.sqlite3` | Accounts, annotation state, revisions, and action history |
| `backend/data/annotation_verification.xlsx` | Automatically updated workbook containing every rater’s work |

The server workbook keeps the source worksheet and its original AI values. It adds:

- **Rater annotations**: one row per account/post that has been edited. Includes account ID, email, source ID/row, stage, revision, timestamps, notes, 44 `HUMAN:<original header>` binary columns, and 44 `AI_REVIEW:<original header>` columns. Human columns mean selected/not selected; the stage and confirmation timestamp distinguish drafts from confirmed choices. AI review cells contain `accepted`, `rejected`, or blank for unreviewed/nonpositive codes.
- **Action log**: timestamped snapshots for every saved annotation action, identifying the rater by email.
- **Codebook**: the exact original header, spreadsheet column, research question, and code type.

**Download my annotations** produces the same structure filtered to the signed-in rater. Other raters’ annotations and action logs are excluded. Source AI cells for that rater’s unconfirmed posts are blanked in this personal download.

SQLite is the durable account store. The server regenerates the XLSX after each annotation action. A shared file lock serializes annotation writes across worker processes; the XLSX is written to a temporary file and replaced atomically. If a workbook write fails, the database retains the action, the UI distinguishes that state, and a sync retry can rebuild the workbook.

To rebuild the XLSX manually:

```bash
.venv/bin/python backend/manage.py sync_workbook
```

Importing the same source file is idempotent. Importing a different source into an existing dataset is refused, preserving existing account annotations. For a separate study, use a separate `DATA_DIR` and `SOURCE_WORKBOOK`. Keep the entire data directory on a persistent local disk; the SQLite/file-lock design is intended for one server, not replicas on separate machines. Back up the database and frozen source together. Open a copy of the output XLSX for analysis so Excel does not lock the live output on Windows.

## Docker deployment

The app uses a Python Django server and a Node.js Next.js server. It cannot run entirely on Sites’ Cloudflare Worker runtime.

```bash
cp .env.example .env
# Replace DJANGO_SECRET_KEY in .env with a random secret.
docker compose up --build -d
```

Visit http://localhost:3106, or http://YOUR_SERVER_IP:3106 for remote access. For remote login, set `CSRF_TRUSTED_ORIGINS=http://YOUR_SERVER_IP:3106` in `.env` before starting the containers. Docker stores account data and the generated workbook in the persistent `annotation-data` volume. The build copies the original study workbook into the backend image at `/input/study.xlsx`, owned by the backend user with read-only permissions. This avoids host-file permission failures when the app imports the workbook. Rebuild the backend image when changing the input workbook. Server port 3106 maps to Next.js container port 3000; Django is internal. To copy the live master workbook:

```bash
docker compose cp backend:/app/data/annotation_verification.xlsx ./annotation_verification.xlsx
```

The Compose project is explicitly named `annotation2`. When diagnosing an existing deployment before copying the updated YAML to the server, use `docker compose -p annotation2 ps -a` and `docker compose -p annotation2 logs --tail=120 backend frontend`. An empty result from plain `docker compose ps` can mean it selected a different project. Direct container logs are also available with `docker logs --tail=120 annotation2-backend-1`.

If an earlier container fails with `PermissionError: '/input/study.xlsx'`, copy the updated `compose.yaml` and `backend/Dockerfile` to the server and run:

```bash
docker compose -p annotation2 up -d --build --force-recreate
docker compose -p annotation2 ps -a
curl -I http://127.0.0.1:3106
```

The updated Compose file removes the input bind mount, so it no longer hides the image's readable workbook. Recreating the containers keeps the existing `annotation-data` volume and its saved accounts and annotations.

For internet hosting, place the app behind HTTPS; configure `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and `DJANGO_COOKIE_SECURE=1` for the actual domain. The provided `.env.example` uses insecure cookies solely to support a local HTTP Docker preview. `DJANGO_DEBUG=0` requires an explicitly configured secret. Add login rate limiting at the reverse proxy when exposing the app to the internet. Set `ALLOW_REGISTRATION=0` to require administrator-created rater accounts:

```bash
docker compose exec backend python manage.py createsuperuser
docker compose exec backend python manage.py shell
# In the Django shell:
# import uuid
# from django.contrib.auth import get_user_model
# get_user_model().objects.create_user(uuid.uuid4().hex, "rater@example.edu", "1")
# The generated identifier is internal; the rater signs in with their email.
```

The default registration setting is open so raters can create their own accounts. Any registered account can read the study posts. Accounts have no cross-rater API or master-workbook download; the study owner accesses the aggregate file on the server.

## Verification

```bash
.venv/bin/python backend/manage.py test annotations --noinput
cd frontend
npm run typecheck
npm run build
```

Browser tests use a separate temporary database and ports 13000/18000, leaving actual rater data untouched:

```bash
# From the project root, use an installed Chrome:
CHROME_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' ./scripts/e2e.sh
# Or install Playwright Chromium first, then run ./scripts/e2e.sh:
# cd frontend && npx playwright install chromium
```

Screenshots and a test workbook download are written to the ignored `test-results/` folder. Docker configuration is supplied but needs a Docker installation to build and validate the containers.

## Stack references

- [Django 5.2 documentation](https://docs.djangoproject.com/en/5.2/)
- [Next.js App Router installation and system requirements](https://nextjs.org/docs/app/getting-started/installation)
