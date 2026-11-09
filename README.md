# Classrabbit

A local UCLA enrollment tracker with a browser interface, FastAPI HTTP API, SQLite watchlist, and background availability checks.

**The public UCLA data source refreshes hourly. This app cannot provide real-time seat alerts.** The existing ClassRabbit service's private data source is unknown. The adapter is replaceable when a fresher approved source becomes available.

## Start the app

Requires Python 3.11 or later. From this repository folder:

```powershell
python -m pip install -e ".[test]"
python -m playwright install chromium
python -m classrabbit.server
```

Open **http://127.0.0.1:8000** for the app or **http://127.0.0.1:8000/docs** for interactive API documentation. The OpenAPI schema is at `/openapi.json`.

Search for **Winter 2027 / COM SCI / 31**, choose a lecture, and add it to your watchlist. You can also paste an exact public UCLA ClassDetail URL. Numeric course search lists lectures; linked discussions and labs are not expanded. Use a direct section link for other course codes or section types.

The server saves watches, observations, and reopening activity to `.local/app.sqlite3`. That directory is excluded from Git. Change the port or database with:

```powershell
python -m classrabbit.server --port 8001 --database .local/another.sqlite3
```

Stop with Ctrl+C. Background checks run while this server process is running, including while the browser is closed. Restarting resumes saved watches. The service checks due watches every 15 seconds, but each successfully checked section is eligible for an upstream check only once per hour. Failed checks back off. **Check section** respects the same cooldown; **Refresh list** reloads saved results without contacting UCLA.

## Implemented

- Responsive watchlist and activity screens with class search, exact section selection, filters, removal, restrictions, source notices, and MyUCLA links.
- Validated HTTP API for search, saved watches, refreshes, activity, and source/health information. See [API reference](docs/API.md).
- Persistent observations and one event per observed closed/waitlisted-to-open transition. The first check creates a baseline, not an alert.
- Shared checks for duplicate watches, a short search cache, unknown availability on failure, preserved historical observations, and scheduled retries.
- A source badge and an explicit hourly freshness notice. Browser tests using simulated data show an additional visible test banner; the production server always uses the public UCLA adapter.

This is a **single-user local app**, bound to loopback. There are no user accounts, outbound email/SMS/push notifications, or hosted deployment. Events appear in the app. Do not expose this server publicly without adding authentication, user isolation, and deployment infrastructure. Run one server process against each database.

An open lecture does not prove that a student is eligible or that its required discussion/lab has space. Current enrollment happens in MyUCLA.

## Testing loop

Run all parser/API/state tests and three Playwright browser iterations:

```powershell
powershell -File scripts/verify.ps1 -Iterations 3
```

Or run the suites individually:

```powershell
python -m unittest discover -s tests -v
python -m tests.browser_suite --iterations 3
```

The browser suite launches real Chromium and real local API servers backed by temporary databases. Only the UCLA data adapter is replaced with a controlled test source. It tests reopenings, error preservation, refresh deduplication, activity, duplicate selection, reload persistence, removal, invalid inputs, stale search results after term changes, source outages, disconnected-server recovery, keyboard dialog dismissal, filters, and mobile layouts. It does not repeatedly poll UCLA. Traces and screenshots go to `.local/browser-tests/`.

For a separate **opt-in live check**, start the normal server in another terminal, then run:

```powershell
python -m tests.live_smoke
```

That check searches real Winter 2027 CS 31, saves Lecture 1 if absent, compares the displayed count to the live app API response, checks persistence and refresh cooldown, captures desktop/mobile screenshots, and opens Swagger docs. It leaves its real watch saved. The term/class ID in that smoke test is a specific historical test case; update it when UCLA no longer offers that term. Outputs are in `.local/screenshots/`.

## Data source and approval

UCLA's public detail page explicitly says section counts update once per hour. During investigation, the public search listing and detail page returned different counts. This adapter consistently uses detail-page counts and preserves the source notice separately from the time the app fetched the page. A time-only source notice is not converted into a precise timestamp.

Requests use public schedule pages only. No student login, private enrollment endpoint, or API credentials are used. This investigation does not establish permission for a production monitoring service or any guarantee of freshness from UCLA.

See [API approval steps and inquiry draft](docs/API_ACCESS.md) for requesting official Classes API access and confirming its update frequency. Fast alerts depend on finding a source with suitable freshness, even after API access is granted.

## Command-line checker

The original checker remains available without the web server:

```powershell
python -m classrabbit --term 27W --subject "COM SCI" --course 31 --list
python -m classrabbit --term 27W --subject "COM SCI" --course 31 --class-id 187093200
python -m classrabbit --term 27W --subject "COM SCI" --course 31 --class-id 187093200 --watch
```

It uses `.local/state.sqlite3` by default, separate from the web app. Add `--json` for machine-readable output or `--state PATH` for a different database.
