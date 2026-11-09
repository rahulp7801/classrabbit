# Classrabbit

A local UCLA enrollment tracker with a browser interface, FastAPI HTTP API, SQLite watchlist, and background availability checks.

**The app checks complete section combinations every 30 seconds by default and streams detected openings to an open browser. UCLA's underlying public-source update delay remains unverified, so an instant alert after a real seat drop is not guaranteed.** The existing ClassRabbit service's private data source is unknown.

For signed-in MyUCLA access, run **`python -m classrabbit.server --source myucla`**. This uses authenticated counts with a 30-second target check cadence and a separate local database. All adapter reads share a minimum 10-second request gap, with persistent cooldowns and access stops. These application limits are not a UCLA-approved quota and do not guarantee immediate detection or prevent all rate limits. See [read-only MyUCLA setup](docs/MYUCLA_READ_ONLY.md). Authentication requires the user's local sign-in; it is not a new campus API permission. Account-derived observations and browser evidence stay in the Git-ignored local directory.

## Start the app

Requires Python 3.11 or later. From this repository folder:

```powershell
python -m pip install -e ".[test]"
python -m playwright install chromium
python -m classrabbit.server
```

Open **http://127.0.0.1:8000** for the app or **http://127.0.0.1:8000/docs** for interactive API documentation. The OpenAPI schema is at `/openapi.json`.

Search for **Winter 2027 / ENGR / 186W** or **Winter 2027 / COM SCI / 31**, choose a lecture, and add it to your watchlist. You can also paste an exact public UCLA ClassDetail URL. Search supports numeric course numbers with optional suffixes and lists lectures. Each lecture check follows its required discussion/lab relationships before reporting a complete seat set. Use a direct section link for prefixed course codes or other section types.

The server saves watches, observations, and reopening activity to `.local/app.sqlite3`. That directory is excluded from Git. Change the port or database with:

```powershell
python -m classrabbit.server --port 8001 --database .local/another.sqlite3
```

Stop with Ctrl+C. Background checks run while this server process is running, including while the browser is closed. Restarting resumes saved watches. The scheduler wakes every second and checks each due lecture and required linked set every 30 seconds by default. Failed checks back off. **Check section** respects that same cooldown; **Refresh list** reloads saved results without contacting UCLA. In-app alerts require an open browser page; there is no phone delivery.

To use a different check interval (15–3600 seconds):

```powershell
$env:CLASSRABBIT_POLL_SECONDS = '15'
python -m classrabbit.server
```

This controls how often the app requests public section summaries. It does not control how often UCLA updates its underlying counts. Descriptions and restriction metadata are cached for an hour; lecture and linked-section counts are requested on each scheduled check.

## Implemented

- Responsive watchlist and activity screens with class search, exact section selection, filters, removal, restrictions, source notices, and MyUCLA links.
- Validated HTTP API for search, saved watches, refreshes, activity, and source/health information. See [API reference](docs/API.md).
- Persistent observations and one event per verified blocked-to-available complete section combination. The first check creates a baseline, not an alert.
- Shared checks for duplicate watches, a short search cache, unknown availability on failure, preserved historical observations, and scheduled retries.
- A source badge separating check frequency from source freshness. Browser tests using simulated data show an additional visible test banner; the production server uses public UCLA section summaries.
- Immediate event streaming to an open browser once the app detects a reopening. This is an in-app alert, not an OS/phone push notification, and it does not make UCLA's source update faster.

This is a **single-user local app**, bound to loopback. There are no user accounts, outbound email/SMS/push notifications, or hosted deployment. Events appear in the app. Do not expose this server publicly without adding authentication, user isolation, and deployment infrastructure. Run one server process against each database.

An open lecture alone does not trigger an opening alert. A positive complete section combination requires the lecture and a compatible seat at every required linked level. Missing or ambiguous relationships stay unknown. Enrollment restrictions and the student's pass remain unverified; current enrollment happens in MyUCLA. Complete-set verification starts from a watched lecture; a standalone discussion link does not establish its lecture's availability.

## Testing loop

Run all parser/API/state tests, three Playwright browser iterations, and ten controlled immediate-alert timing trials:

```powershell
powershell -File scripts/verify.ps1 -Iterations 3
```

Or run the suites individually:

```powershell
python -m unittest discover -s tests -v
python -m tests.browser_suite --iterations 3
python -m tests.immediate_proof --iterations 10
python -m tests.scheduled_proof
```

The browser suite launches real Chromium and real local API servers backed by temporary databases. Only the UCLA data adapter is replaced with a controlled test source. It tests reopenings, error preservation, refresh deduplication, activity, duplicate selection, reload persistence, removal, invalid inputs, stale search results after term changes, source outages, disconnected-server recovery, keyboard dialog dismissal, filters, and mobile layouts. It does not repeatedly poll UCLA. Traces and screenshots go to `.local/browser-tests/`.

The immediate-alert proof holds the ENGR 186W lecture at 10 seats and changes one required discussion from closed to open, using the real linked-section parser, API, event stream, and Chromium. The separate scheduled proof waits for an actual 30-second worker cycle, without advancing a test clock or manually refreshing. Both use controlled discussion data and do not prove UCLA publication latency. See [research findings and evidence](docs/REALTIME_RESEARCH.md).

For a separate **opt-in live check**, start the normal server in another terminal, then run:

```powershell
python -m tests.live_smoke
```

That check searches real Winter 2027 CS 31, saves Lecture 1 if absent, compares the displayed count to the live app API response, checks persistence and refresh cooldown, captures desktop/mobile screenshots, and opens Swagger docs. It leaves its real watch saved. The term/class ID in that smoke test is a specific historical test case; update it when UCLA no longer offers that term. Outputs are in `.local/screenshots/`.

## Data source and approval

UCLA's public detail page explicitly says its counts update once per hour. During investigation, the public search listing and detail page returned different counts. The web app now requests primary and required child counts from the public `GetCourseSummary` endpoint on each check, and keeps detail-page notices as contextual metadata. Its observed HTTP `no-cache` header does not prove the database is current. A time-only source notice is not converted into a precise timestamp.

Requests use public schedule pages only. No student login, private enrollment endpoint, or API credentials are used. This investigation does not establish permission for a production monitoring service or any guarantee of freshness from UCLA.

See [API approval steps and inquiry draft](docs/API_ACCESS.md) for requesting official Classes API access and confirming its update frequency. Fast alerts depend on finding a source with suitable freshness, even after API access is granted.

## Command-line checker

To investigate the signed-in MyUCLA data source, run `python -m classrabbit.myucla_probe`. It opens an isolated visible browser for manual UCLA sign-in and Duo. Open Find a Class and Enroll, choose Winter 2027 ENGR 186W, and expand Lecture 1's discussions. The helper records only the specified lecture/linked rows and a limited set of read-response metadata under `.local/myucla-probe/`; its browser profile is Git-ignored. This is a source inspection helper, not an implemented authenticated monitoring adapter or proof of instant source updates.

The original diagnostic checker remains available without the web server. It uses detail-page counts and an hourly interval; use the server for the fast summary adapter:

```powershell
python -m classrabbit --term 27W --subject "COM SCI" --course 31 --list
python -m classrabbit --term 27W --subject "COM SCI" --course 31 --class-id 187093200
python -m classrabbit --term 27W --subject "COM SCI" --course 31 --class-id 187093200 --watch
```

It uses `.local/state.sqlite3` by default, separate from the web app. Add `--json` for machine-readable output or `--state PATH` for a different database.
