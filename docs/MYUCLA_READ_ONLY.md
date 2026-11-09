# Read-only MyUCLA access

The app can read section counts through the same authenticated GET route used by the signed-in MyUCLA interface:

`GET https://sa.ucla.edu/ro/ClassSearch/Results/GetCourseSummary`

This uses the user's existing local browser session. It does not issue a UCLA API key or grant additional university permissions. The local adapter allows only this read endpoint, follows no redirects on API calls, and rejects enrollment routes, arbitrary hosts, and unexpected request fields.

## Start

```powershell
python -m pip install -e ".[myucla]"
python -m playwright install chromium
python -m classrabbit.server --source myucla
```

The dedicated browser reuses `.local/myucla-probe/browser-profile/`. Complete UCLA login and Duo manually when needed. Keep that browser running and signed in. The profile and runtime data are Git-ignored.

Run either the inspection helper or this service at a time; they share the same isolated browser profile.

Open `http://127.0.0.1:8000` and add Winter 2027 ENGR 186W Lecture 1, class ID `186827200`. This mode uses a separate local database, `.local/myucla-app.sqlite3`. It supports up to five watches in this prototype.

The authenticated mode targets a **two-second check cadence**, with one request at a time. Request duration and network delays affect the observed period. An overlong request is followed by a short gap, not an overlapping request. Retries back off; HTTP 429 responses honor the server's retry cooldown. Optional setting: `CLASSRABBIT_MYUCLA_POLL_SECONDS` from 2 to 60.

Each request includes the site's AJAX request header and a generated cache-busting timestamp. These are normal read-request mechanics; authorization still comes from the user's signed-in session. Source freshness is not inferred from a cache header.

## Your local API

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/source
Invoke-RestMethod http://127.0.0.1:8000/api/watchlist
```

`/api/source` reports `id: ucla_myucla`, `session_status`, and `check_interval_seconds: 2`. `/api/watchlist` returns the current observations. The browser app receives complete-set opening events through `/api/events/stream`.

Local watchlist POST/DELETE operations change local preferences only. They do not enroll or drop classes. Session cookies and passwords are not returned through the local API or written into Git. Descriptions and course-identity metadata still come from the public schedule; the availability counts in this mode come exclusively from the authenticated route.

If sign-in expires, requests redirect, the browser closes, or MyUCLA returns an error, availability becomes unknown and the last successful observation is marked historical. The adapter does not substitute public cached availability. Reauthenticate in the local browser or restart this mode as needed.

## Verification and data handling

Automated tests use synthetic responses and cover read-route restrictions, source precedence, expired sessions, private-link normalization, and fail-closed availability. No authenticated page response, account-derived count, screenshot, cookie, or browser profile is included in this repository.

Account-specific inspection results remain under `.local/myucla-probe/`. A successful read demonstrates transport access; it does not measure the delay between a real dropped seat and MyUCLA updating. The controlled tests measure application delivery after a detected complete-set opening.

The browser page must stay open for in-app alerts. Phone/push delivery has not been configured. Keep the local server bound to loopback; this prototype has no multi-user account isolation.
