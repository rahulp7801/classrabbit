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

The authenticated mode targets a **30-second check cadence**, with one request at a time. All automated adapter reads, including public metadata/search and authenticated lecture/discussion/lab reads, share a minimum **10-second gap plus up to one second of jitter**. This allows at most six request starts per rolling minute for this instance. A check needing several requests takes longer than a single lecture read. Manual refresh, adding another watch, and restarting cannot erase the shared request deadline. Optional setting: `CLASSRABBIT_MYUCLA_POLL_SECONDS` from 30 to 3600; two-second polling is no longer accepted.

These are conservative application limits, **not a published or approved UCLA quota**. They reduce request volume but cannot guarantee protection from rate limiting, account restrictions, or missed short-lived openings. Traffic from manually using the MyUCLA browser and other apps is outside this adapter's budget. Do not run multiple independent profiles or instances to multiply the request rate.

The safety state contains only deadlines, counters, and pause reasons in `.local/myucla-probe/read-safety.json`. It is saved before each request. Keep this file across restarts; missing or corrupted fields fail closed. Do not delete safety state to bypass an access block or cooldown.

- HTTP 429: pause for at least 15 minutes, or longer when `Retry-After` specifies a larger delay. Both seconds and HTTP dates are supported. A second 429 stops monitoring for review instead of repeatedly retrying.
- HTTP 403: stop automated reads; consult UCLA about permitted access. A restart does not clear the stop.
- Redirects or HTTP 401: pause until the user signs in manually and explicitly resumes checks in the app. The adapter does not follow API redirects or automate login/Duo.
- Other HTTP/network failures: persistent exponential cooldown starting at 60 seconds, up to one hour, with jitter and any longer server retry delay.
- Corrupted or unwritable safety state: stop rather than sending unbudgeted requests.

Cooldowns and stops apply before metadata requests as well as section reads. All watched availability becomes unverified during a hold. The local UI displays the hold and a sign-in resume button when appropriate. No button clears a rate/access hold.

Each request includes the site's AJAX request header and a generated cache-busting timestamp. These are normal read-request mechanics; authorization still comes from the user's signed-in session. Source freshness is not inferred from a cache header.

## Your local API

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/source
Invoke-RestMethod http://127.0.0.1:8000/api/watchlist
```

`/api/source` reports `id: ucla_myucla`, `session_status`, `check_interval_seconds: 30` by default, and a `safety` object containing the minimum gap, next-request timestamp, cooldown deadline, and pause reason. `/api/watchlist` returns the current observations. The browser app receives complete-set opening events through `/api/events/stream`. `POST /api/source/resume` clears only a sign-in pause after manual authentication; it does not skip pacing, cooldowns, HTTP 403 stops, or repeated rate limits.

Local watchlist POST/DELETE operations change local preferences only. They do not enroll or drop classes. Session cookies and passwords are not returned through the local API or written into Git. Descriptions and course-identity metadata still come from the public schedule; the availability counts in this mode come exclusively from the authenticated route.

If sign-in expires, requests redirect, the browser closes, or MyUCLA returns an error, availability becomes unknown and the last successful observation is marked historical. The adapter does not substitute public cached availability. After reauthentication, use the sign-in resume button. Persistent access/rate stops need review with UCLA before any further automated access.

## Verification and data handling

Automated tests use synthetic responses and cover read-route restrictions, source precedence, expired sessions, private-link normalization, shared pacing across restarts, HTTP-date retry headers, persistent rate/access stops, manual resume restrictions, metadata throttling, and fail-closed availability. No authenticated page response, account-derived count, screenshot, cookie, or browser profile is included in this repository.

Account-specific inspection results remain under `.local/myucla-probe/`. A successful read demonstrates transport access; it does not measure the delay between a real dropped seat and MyUCLA updating. The controlled tests measure application delivery after a detected complete-set opening.

The browser page must stay open for in-app alerts. Phone/push delivery has not been configured. Keep the local server bound to loopback; this prototype has no multi-user account isolation.

## Published guidance checked November 9, 2026

UCLA's [DTS Acceptable Use Policy](https://dts.ucla.edu/it-policies-practices/online-acceptable-use-policy) prohibits interfering with normal system operation, excessive load, and circumventing protection schemes. It does not specify a numeric quota for this MyUCLA route. No numeric quota was found in the official material reviewed; that is not evidence that automation is approved.

The official [Classes API](https://developer.api.ucla.edu/api/261) requires Registrar's Service Request approval. The [API portal help](https://developer.api.ucla.edu/index.php/node/31) explains app/product approval and lists `api-support@it.ucla.edu`. Ask the owner about permitted polling, data freshness, and whether an event feed is available before promising reliably immediate detection. An approved API alone does not establish an instant update guarantee.
