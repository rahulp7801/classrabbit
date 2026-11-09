# Immediate alerts: findings and measured proof

## What is proven

The application now pushes a detected reopening to connected browsers using a server-sent event. It no longer waits for the browser's 30-second dashboard refresh to show that alert. Reopening events are persisted before publication; repeated open observations do not generate another event. Reconnecting browsers can request replay after their last event ID.

The controlled ENGR 186W test ran ten closed-to-one-seat-open transitions through a real local FastAPI server, SQLite state, the event stream, and real Playwright Chromium. From the test source adapter returning an opening to the browser visibly displaying its alert, the measured median was **33.81 ms**, with a maximum of **39.30 ms**. These measurements include the automation assertion overhead and apply to this local run, not a deployment latency guarantee.

**Those transitions were simulated. No UCLA enrollment was changed. This does not prove the time between an actual dropped seat and UCLA publishing that change. No phone notifications were configured or sent.**

- [Recorded timing samples](evidence/engr186w-immediate-timings.json)
- [Screenshot with the simulated-data banner](evidence/engr186w-controlled-alert.png)
- Reproduce: `python -m tests.immediate_proof --iterations 10`
- Raw trace: `.local/immediate-proof/trace.zip`

The test uses a captured public response for the real section's identity and metadata, then controls only the availability and test clock. It triggers a source check through the real refresh API; the browser receives an unsolicited event without clicking Refresh. There are no production endpoints for injecting fake seat counts.

## Exact requested class

Winter 2027 ENGR 186W, Ethics for Computer Scientists, Lecture 1:

- Class ID: `186827200`
- UCLA catalog value: `0186W   ` (padding matters to the source)
- [Public class detail](https://sa.ucla.edu/ro/Public/SOC/Results/ClassDetail?term_cd=27W&subj_area_cd=ENGR%20%20%20&crs_catlg_no=0186W%20%20%20&class_id=186827200&class_no=%20001%20%20)
- Observed at `2026-11-09T08:02:46Z`: the public listing, summary, and detail responses all reported **10 seats left out of 120**.
- Reported restrictions: sophomores, juniors, or seniors; undergraduate Computer Science or Computer Science and Engineering majors.

This is a historical public report, not confirmation that the user can currently enroll. The detail page's source notice said status updates once per hour and displayed a time of day without a source date. A user's enrollment pass, prerequisites, restrictions, and any linked sections still matter. Confirm in [MyUCLA](https://my.ucla.edu/directLink.aspx?featureID=203).

The Samueli advising office independently lists ENGR 186W for Winter 2027 in its [school-wide course offerings](https://www.seasoasa.ucla.edu/engr-courses/).

## Data freshness research

| Source | Evidence | What it establishes |
| --- | --- | --- |
| Public `ClassDetail` page | UCLA's page explicitly says counts update once per hour. | It is unsuitable for proving immediate detection of a real dropped seat. |
| Public course-title listing | The inspected response had `Cache-Control: public, max-age=14400`. | A successful HTTP fetch is not evidence that the enrollment counts just changed. |
| Public `GetCourseSummary` endpoint | The inspected response had `Cache-Control: no-cache`; ENGR 186W matched the listing. | Promising for investigation, but `no-cache` is an HTTP cache instruction, not a freshness SLA for the underlying enrollment database. |
| MyUCLA Find a Class and Enroll / Class Planner | [ASUCLA says these show real-time enrollment counts](https://www.asucla.ucla.edu/ucla/ucla-schedule-of-classes). | This is an officially documented place to verify a change against a signed-in student view. We have not inspected or monitored the user's signed-in session. |
| Official Classes API | [UCLA's product page](https://developer.api.ucla.edu/api/261) exposes class/section enrollment information and requires RSR approval. | A supported integration route, subject to approval. Its public documentation does not promise a particular update frequency; that must be confirmed. |

One ENGR 186W summary request completed in 54.55 ms and its detail request in 154.12 ms. Those are request durations, not measurements of how recently the underlying data changed. In a separate CS 31 comparison, the summary and detail disagreed on counts. Without an independent reference, that difference cannot establish which was fresher.

## What other projects demonstrate

We inspected the original source, not just descriptions:

- [`66lej/bruin-class-alert`](https://github.com/66lej/bruin-class-alert/blob/main/bruin_alert.py) queries the public Schedule of Classes results page; its [README](https://github.com/66lej/bruin-class-alert/blob/main/README.md) recommends polling no faster than every 60 seconds. This demonstrates a polling/notification implementation, not an uncached real-time source.
- [`aaditsingh07/ucla-soc-mcp`](https://github.com/aaditsingh07/ucla-soc-mcp) uses the public AJAX course-summary endpoint. Fetching that endpoint on each request does not independently prove the registrar data is newly updated.
- [ClassRabbit's own site](https://classrabbitapp.com/) advertises immediate notifications but does not publish the backend or enrollment data source. We cannot infer API approval or an uncached feed from that claim.

No third-party scraper code was executed or copied into the application.

## Practical next step for genuinely fast detection

For a supported always-on service, request official Classes API access and ask specifically about update latency, permitted polling frequency, enrollment restrictions, and whether change events/webhooks exist. Approval alone does not demonstrate adequate freshness.

For a personal prototype, a read-only adapter running in the user's own signed-in MyUCLA browser is another route to investigate. It would need manual sign-in/Duo, a verified way to refresh the exact section, and a permitted request interval. Browser sleep and session expiration would prevent an unattended reliability guarantee. No such authenticated adapter is implemented or verified yet.

To prove end-to-end UCLA detection, compare naturally occurring changes for class `186827200` against the independently refreshed signed-in MyUCLA count. Record when each source first exposes the same change and when the browser alert appears. Do not create enrollment transactions to manufacture a test event.

The useful latency budget is:

`UCLA publication delay + permitted check interval + check duration + event/notification delivery`

Only the application event-delivery portion has a controlled sub-second measurement today. The default public adapter remains hourly; we did not simply shorten the timer and label cached data real time.

## Push delivery research

Phone setup was deferred at the user's request. For a later implementation, [Pushover's API](https://pushover.net/api) supports a message with an enrollment link and priority. [ntfy](https://docs.ntfy.sh/subscribe/phone/) is another option with platform-specific delivery behavior. Neither service can compensate for a stale UCLA source, and neither provides an absolute guarantee that a device will display every message immediately.

The current event stream is an **in-app alert while the page is open**, not an OS push notification or a background phone alert. The browser displays whether that connection is active.
