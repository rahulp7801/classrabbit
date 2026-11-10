# Local HTTP API

Start `python -m classrabbit.server`. Base URL: `http://127.0.0.1:8000`.

Interactive documentation: `/docs`. Machine-readable schemas: `/openapi.json`. This API serves one local workspace; it does not access UCLA's restricted official API.

| Method | Route | Behavior |
| --- | --- | --- |
| GET | `/api/health` | Version and whether the background scheduler thread is running |
| GET | `/api/source` | Source identity, live/test mode, check interval, and separate underlying freshness information |
| GET | `/api/sections?term=27W&subject=COM%20SCI&course=31` | Find lecture section IDs and public detail links; searches cached for five minutes |
| GET | `/api/watchlist` | Saved watches with last good snapshots, errors, stale flags, and next check times |
| POST | `/api/watchlist` | Save and initially check a section; JSON body: `{"source_url":"FULL_PUBLIC_CLASSDETAIL_URL"}` |
| POST | `/api/watchlist/{id}/refresh` | Check when due; otherwise return the saved result with `cached: true` |
| DELETE | `/api/watchlist/{id}` | Stop watching a section; historical activity remains |
| GET | `/api/events?limit=50` | Latest reopening events, newest first; limit 1–100 |
| GET | `/api/events/stream?after=0` | Server-sent reopening events for connected clients; supports `Last-Event-ID` replay |

## Example in PowerShell

```powershell
$base = 'http://127.0.0.1:8000'
$sections = Invoke-RestMethod "$base/api/sections?term=27W&subject=COM%20SCI&course=31"
$body = @{ source_url = $sections.sections[0].url } | ConvertTo-Json
$watch = Invoke-RestMethod "$base/api/watchlist" -Method Post -ContentType 'application/json' -Body $body
Invoke-RestMethod "$base/api/watchlist/$($watch.id)/refresh" -Method Post
Invoke-RestMethod "$base/api/events"
```

## Response behavior

- A new watch returns `201`. Adding the same term/class ID returns the existing watch with `200`, without creating a duplicate check.
- A failed initial check still saves the watch with a null `snapshot`, an `error`, and a scheduled retry. A later failed check preserves the last successful `snapshot` and marks the item `stale`. An HTTP `200` watch response must not be read as proof of availability: inspect `error`, `stale`, and `snapshot`.
- A refresh returns `{watch, cached, reopened}`. `reopened` is true only when a previously verified blocked complete set becomes available. The lecture may remain open while a required discussion opening causes this transition. Activity entries are local records, not notification delivery receipts.
- Fetched times and scheduled times use ISO 8601 UTC. `source_notice` contextualizes public summary counts and preserves UCLA's detail-page time-only notice as metadata. `realtime` is false.
- `stale` is true when there is no snapshot, a source check failed, or the last successful fetch is at least one configured check interval old. An unstale fetch can still contain UCLA's cached data or an unknown complete-set result; inspect `combination_status` separately.
- `checking` is true while the serialized upstream check for that watch is in progress. Saved watchlist, activity, source, and health reads remain available during slow checks; they do not wait for the network request or initiate another upstream read. The dashboard excludes stale reports from open/waiting totals and filters, hides their current seat count, and labels preserved results as previous reports. It also expires reports locally between dashboard reloads.
- Valid public URLs must identify exactly one term, subject, catalog number, class ID, and class number. Arbitrary hosts and login URLs are rejected. The local prototype supports 25 watches.
- Invalid query/body fields return `422`; invalid public links return `400`; unknown watch IDs return `404`; the watch limit returns `409`; course-search source failures return `502`.

Course search supports a numeric base with a suffix, such as `31` or `186W`. Prefixed course codes still require a direct public section link.

The event stream sends `connected` and `reopening` events, with the persisted event ID in the SSE `id` field. Reconnection replays up to the latest 100 stored events after the supplied cursor. Heartbeats keep idle connections open. Slow connected clients have a bounded 100-event buffer; activity remains persisted separately. Streaming reduces application delivery delay; it does not change the source update frequency or deliver OS notifications.

Each snapshot includes `combination_status` (`available`, `blocked`, or `unknown`), `combination_reason`, `linked_sections`, and `open_combinations` (compatible paths of class IDs starting with the lecture). The section's own `status` and `available` remain separate. A lecture can report `open` and 10 seats while `combination_status` is `blocked` because all required discussions are closed. Unknown checks preserve the last verified combination baseline for transition detection. Legacy lecture-only history is labeled `legacy_lecture_opening` and is not streamed as a new combination alert.

`check_interval_seconds` is 30 by default, configurable with `CLASSRABBIT_POLL_SECONDS` between 15 and 3600. `refresh_interval_seconds` is null because the underlying summary-data update frequency is unverified. The scheduler wakes every second. It requests primary and child availability at each due check, while reusing descriptions/restrictions for up to an hour.

With `--source myucla`, the adapter uses the user's isolated signed-in browser session and the observed read-only ClassSearch endpoint. The source ID is `ucla_myucla`, `session_status` reports connection state, and the default target check cadence is 30 seconds. All adapter reads share a minimum 10-second gap plus jitter, including public metadata and linked sections. Longer checks and upstream publication delays affect detection time. The `safety` object reports `minimum_request_gap_seconds`, `pause_reason`, `cooldown_until`, `next_request_at` (Unix seconds), `rate_limit_responses`, and `university_approved_quota: false`. These limits are application defaults, not a UCLA-approved quota.

HTTP 429 applies a persistent cooldown of at least 15 minutes and any longer `Retry-After`; repeated 429 or HTTP 403 stops reads for review. HTTP 401 and redirects pause requests until manual authentication and `POST /api/source/resume`. Explicit sign-in resume reschedules watches at the earliest time allowed by the shared request deadline and cooldown instead of waiting out generic failure backoff. Previous errors remain until a successful check. That endpoint returns 409 for other holds and cannot bypass request spacing or cooldowns. No authenticated data or credentials are returned by the resume route. Watchlist observations are unverified during source holds. See [authenticated read-only access](MYUCLA_READ_ONLY.md). A missing/expired session returns unknown availability; there is no fallback to public counts.

The background worker and manual refresh share the configured check cooldown and durable transition history. Removing and re-adding a section establishes a new baseline. The API and browser share the same server origin; cross-origin browser mutations are rejected. There is no authentication or multi-user isolation, so keep the default loopback binding.
