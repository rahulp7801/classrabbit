# UCLA Classes API access

Sources checked November 8, 2026 (Pacific time):

- [Classes API product](https://developer.api.ucla.edu/api/261): enrollment and section information; access requires approval through a Registrar's Service Request (RSR).
- [Registrar's Reports and Data Requests](https://registrar.ucla.edu/faculty-staff/registrar-data-and-systems-access/reports-and-data-requests): the current RSR entry point is MyUCLA. Use the linked form named **Mass E-mail, New and Modified Reports, API and SSO Integration** to request API access.
- [API Developer Portal](https://developer.api.ucla.edu/): API discovery and access information.
- [Developer portal help](https://developer.api.ucla.edu/index.php/ucla-help): sign in with a UCLA logon, create an individually owned My App or Team App, select the Classes product, and wait for product approval. Approved products show as enabled; use the app's credentials according to that product's current documentation. `api-support@it.ucla.edu` is the published portal support contact.

The Registrar says RSR submitters must be authorized personnel with UCLA logon credentials, an OASIS logon ID, and completed FERPA training. OASIS/SAWeb access requests go through the departmental security administrator. These instructions do not establish that an independent student or unaffiliated developer is eligible.

If you do not already have that authorization, contact the Registrar through its official contact page and ask about eligibility and whether a UCLA department sponsor is required. A normal student MyUCLA login alone should not be assumed sufficient.

## Suggested inquiry text

This is a draft for you to review and send. No inquiry or access request has been submitted.

> I'm developing an independent UCLA course availability notification app. Students would select specific sections and receive an alert when availability changes; they would enroll through MyUCLA themselves.
>
> I'd like to request read-only access to the Classes/Class Sections API for term codes, course and section identifiers, enrollment status, capacity, available seats, waitlist status, and enrollment restrictions. I do not need student identities, individual schedules, rosters, or enrollment write access.
>
> Is this use eligible for access? If I am applying as an independent developer/student, do I need a UCLA department sponsor, and who should submit the RSR? What application registration steps follow approval?
>
> Could you also confirm the data refresh frequency, permitted request rate, whether availability changes can be subscribed to, and whether showing availability and sending opt-in alerts are permitted? The public schedule detail pages state that counts update hourly, so source freshness is central to this project's feasibility.

Before calling an approved API, confirm its actual response schema and freshness. Do not assume an official API is real time simply because it is an API. Keep credentials in server-side configuration, never in Git.

See [measured alert proof and source research](REALTIME_RESEARCH.md) for the exact Winter 2027 ENGR 186W section and the remaining freshness verification work.
