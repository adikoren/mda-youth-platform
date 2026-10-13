# Implementation decisions (reversible technical details)

These record how approved requirements were implemented where DESIGN_REVIEW.md leaves a technical choice open. None of them reopens an approved product decision. Items marked **[product question]** depend on an open item in DESIGN_REVIEW §9 and are a documented default until Adi decides.

Date: 2026-10-12 (milestones A, B and C; D-27 decided, D-43–D-45 added the same day).

## Data and time

- **D-01 Weekday numbering.** 0 = Sunday … 6 = Saturday everywhere (API, DB, templates). This matches PostgreSQL `EXTRACT(DOW)`, so SQL predicates and Python agree.
- **D-02 Local time.** Instants are stored as `timestamptz` in UTC. Product times are built as local wall-clock values in Asia/Jerusalem first and then converted, never "UTC + N hours". The test suite covers the DST change on 24/25 Oct 2026.
- **D-03 Guest window boundary.** Ordinary guests see the full date schedule until the **exclusive** next local midnight, computed in SQL with the PostgreSQL tz database. This means 23:59:59 is allowed and 00:00 is denied.
- **D-04 Registration templates.** There is one current row per weekday, and edits create a new row (history kept). Effective times are copied onto each activity date when it is created. Holidays use the template of their weekday, which reproduces the 5/2/1 rule and the Friday-14:00 opening exception.
- **D-05 Day kind changes.** Kind is classified manually: regular / friday / saturday / holiday / holiday_eve. Because shift structure depends on kind, kind can only change before any registration or schedule exists for that date. There is no holiday provider yet.

## Accounts and access

- **D-06 Day-manager authority = current `manager_day_scopes` rows.** There is no separate `day_manager` row in `authority_roles`, so a single source of truth avoids drift. `authority_roles` holds `admin` and `youth_manager`. Appointing requires current level *mentor*. Lowering a current day manager's level away from mentor is refused until the appointment is removed.
- **D-07 Sessions.** The cookie holds a random token and the DB stores only its SHA-256. Activity is refreshed at most every 5 minutes. Requests with header `X-MDA-Background: 1` (polling) never refresh it. Idle expiry is 30 days, the cookie max-age is 30 days, and logout revokes the server row.
- **D-08 CSRF.** Double-submit cookie `mda_csrf` plus header `X-CSRF-Token` is required on every non-GET request. In development the browser talks only to Vite, which proxies `/api`, so cookies stay first-party (no CORS).
- **D-09 Login rate limit.** In-process sliding window (8 failures / 15 min per email+IP). Valid for a single API process; it needs a shared store if the API is ever scaled out.
- **D-10 Email.** Format check only (no deliverability check), so synthetic `.test` addresses work.
- **D-11 Pending users** can log in and see only their own profile. All other endpoints return 403 `account_not_active`.
- **D-12 Admin interface concealment.** `/api/admin/*` returns 404 to non-admins. An admin cannot remove their own admin role.
- **D-13 Contact details.** People search returns name/level/home day only. Phone numbers appear on published schedules only for managers of that date and admins. Contact visibility for volunteers is still a product decision (original spec §9).
- **D-14 Approval delegation.** Approval is admin-only for now (initial rollout). `policies.can_approve_signups` is the single place to extend it per scope later.

## Scheduling

- **D-15 Versions.** Opening the manager board ensures one working draft. It is a copy of the latest publication, or baseline slots (capacity 3) the first time. Each edit is saved immediately; that is the "autosave". `edit_count > 0` means dirty.
- **D-16 Immutability.** A PostgreSQL trigger rejects INSERT/UPDATE/DELETE on `ambulance_slots`/`assignments` whose version is not `draft`. Removing someone from a draft deletes the draft row. Published rows remain as history.
- **D-17 Slot identity.** The API addresses slots by `slot_key` (stable across versions), never by row id, so a client cannot target a published row.
- **D-18 Concurrency.** Every draft mutation and publication locks the `activity_days` row (`FOR UPDATE`). This serialises cross-slot rules (same person twice in a shift, trainee reinforcement+evening). The unique constraint `uq_assignment_person_shift` is the DB safety net. Capacity is never locked, only reported.
- **D-19 Publish idempotency.** The client sends `draft_id` + `expected_edit_count`. A stale count returns 409 `stale_draft`. Re-sending for an already published draft returns `already_published: true` with no state change or messages. Publishing an untouched copy of the live schedule returns 409 `nothing_to_publish`.
- **D-20 Pools.** Ordinary evening = self availability. Reinforcement = trainees available for that date's evening, plus manager-added people. Ordinary morning = manager-added only. Special-day morning/evening = self availability. Manager-added people are also in every pool. Assigning someone not in a pool records them as manager-added, so provenance is never lost.
- **D-21 Moves.** Assigning a person who already holds a slot in that shift returns 409 `already_in_shift`. A separate *move* action replaces their slot.
- **D-22 Manual order** is stored per shift (`queue_order`). It is not versioned, so it survives cancellation and republication.
- **D-23 Withdrawal attention.** It is created when an assigned person (in the live publication) withdraws. It stays pending until a manager marks it handled, or until a publication no longer contains that assignment (auto-resolved with reason).
- **D-24 Invitations.** They expire at local midnight after the date. Answering availability confirms a personal invitation. Direct addition of a guest creates a confirmation request; confirming is optional and there is no refusal flow. Expiry never removes a published assignment.
- **D-25 Registration after the date** is refused (`day_ended`). An arrival time is accepted only for an ordinary evening.

## Notifications and worker

- **D-26 Outbox statuses.** `queued` → `accepted` (a real provider accepted it) | `failed` (after 5 attempts, exponential backoff) | `simulated` (no phone channel connected). Nothing is ever reported as *delivered*. Payloads contain ids/dates only.
- **D-27 Publication recipients — decided 2026-10-12 (DESIGN_REVIEW §10.7).** The provisional assigned-only rule is removed.
  - **When:** only inside the publish transaction. Draft edits, cancelling a slot in a draft and idle drafts never queue volunteer messages.
  - **Affected shift:** on the first publication, every shift. On a republication, a shift whose crew was added/removed/moved or whose set of active ambulances changed (a capacity-only change is not a crew change). If someone's edits cancel each other out, nothing is sent.
  - **Recipients per affected shift:** newly assigned (`assigned`), removed (`unassigned`), still assigned (`schedule_changed`) and waiting. Waiting = the board's derived list (pool − assigned in the new version; manager-added included, withdrawn availability excluded). Waiting people get `schedule_published` on the first publication, `new_ambulance` when that shift gained an ambulance, otherwise `schedule_changed_waiting`. Inactive users are skipped.
  - **Deduplication:** one message per person per publication, dedup key `publication:<version>:<user>` (unique in the DB). If a person is affected in several shifts, the type is the most personal one (assigned > unassigned > schedule_changed > new_ambulance > schedule_changed_waiting > schedule_published), and `payload.shifts` lists each shift with its change. A retried publish returns `already_published` and adds nothing; a later publication has a new version id and notifies again.
  - **Payload:** ids/dates/shift kinds only. Someone who was only removed gets `{date, shifts}` without schedule references. A notification never grants schedule access; the normal visibility rules still apply.
- **D-28 Reminders** are derived from DB state on each worker pass, so they survive restarts. (The closing-soon reminder and its `MDA_CLOSING_SOON_HOURS` setting were removed by decision §10.6.)
  - registration opened, for openings in the last 2 days, to eligible volunteers;
  - publication target, to the day's managers, never publishing;
  - dirty draft idle for 10 minutes, to the last editor. One reminder per idle period: the dedup key includes `last_edit_at`, so a new edit followed by more idleness produces exactly one more.

## Journal and mentoring (milestone C)

- **D-29 Two linked parts.** `rides` holds the trainee part (short case description, max 200 chars, with its own `description_version`). `ride_assessments` holds the mentor part: four markers plus a per-case summary, with its own `version`. They share the ride id. Each part has optimistic versioning, so a description edit never overwrites an assessment, or vice versa. A stale write returns 409 `version_conflict` together with the server's current copy.
- **D-30 Markers.** These are stored as jsonb `{blood_pressure|pulse|breathing|forms: success|failure|not_performed|partial}`. An absent key means "not selected yet". `not_performed` (חסר) is a completed answer. Unknown keys or values are rejected with 400.
- **D-31 Ride identity and retries.** The client generates the ride UUID, and `POST /api/journal/rides` is idempotent: the same id for the same trainee/shift returns the saved ride (200, `created: false`). The same id for a different trainee/shift returns 409 `ride_id_conflict`. Creation locks the trainee row, so concurrent retries create one ride. The sequence number (`max+1`, unique per trainee) is allocated on the first server save. It is display order only; it does not guarantee chronology when offline drafts arrive late.
- **D-32 Who may create.** The trainee may create rides only for shifts they were assigned to in the published schedule, from the start of that date. A mentor may create rides for a shift inside their access window or an extension. A ride created by a mentor has no description and shows "ממתין לתיאור שלך" to the trainee. Admin may create rides for any trainee and shift.
- **D-33 Formal mentor** = current level `mentor` (day managers included), in the same ambulance as the trainee in the date's **latest published** version, on an **evening** shift. Draft assignments grant nothing.
- **D-34 Mentor window.** The window runs from 13:30 on the shift date until 10:00 the next local day. It is built in local time, so it is DST-safe. During it, the mentor reads all of the trainee's per-case records and edits only the current shift's assessments and mentoring summary. They never see other shifts' summaries. Morning and reinforcement shifts grant no mentor access.
- **D-35 Reopened access** = a `mentor_access_extensions` row (mentor, trainee, shift; 1–72 h, default 12 h in the UI), granted by the trainee's current home-day manager or by an admin. It is revocable and audited.
- **D-36 Shift mentoring summary** = one row per trainee per shift. Each field is text or `*_none = true` ("אין הערות"); a DB check forbids both at once. Writes use optimistic versioning.
- **D-37 Completeness** is computed per trainee and shift:
  - Trainee part: every ride has a description. A shift with no rides needs nothing from the trainee.
  - Mentor part (only when a formal mentor exists): every ride has all four markers selected plus a per-case summary, **and** the shift summary has both fields completed, even if there were no rides.
- **D-38 Journal reminders** fire at shift end until 09:00 the next morning (phase `end`), and from 09:00 to 12:00 (phase `morning`), only while incomplete.
  - The trainee is reminded only when descriptions are missing.
  - Each formal mentor is reminded when the mentor part is missing.
  - Dedup is one per phase. A late worker pass after a phase's period sends nothing.
  - Trainee reminders also apply to morning and reinforcement shifts where the trainee recorded rides. Mentor reminders exist only for evening shifts.
- **D-39 Progress** = number of server rides + sum of `count_delta` corrections. Last ride date = the latest ride's `occurred_on`. If a correction sets a date, the latest such correction replaces it, and only rides created after that correction can move it later. Corrections are made by the current home-day manager or admin, with a reason, and are audited. Progress is readable by the trainee, all managers (day, youth, admin) and mentors in their window. It is shown on the manager board as context, never as a priority formula.
- **D-40 Personal notes.** Author = trainee.
  - Unshared notes: visible to the author and admins (admin reads are audited admin-only).
  - Shared notes: also visible to the author's current home-day manager, until unshared.
  - Managers see only shared notes, with no counts and no hint that others exist. Note audit rows are `admin_only`. Mentors never see notes.
  - The UI states this privacy rule explicitly; it does not claim trainee-only privacy.
- **D-41 Offline drafts** live in IndexedDB, in one database per user id (`mda-journal-<userId>`), so a shared phone never shows another account's drafts.
  - Every journal edit is written locally first, then synced. Sync runs on page open, on the `online` event and every 30 s while the page is open; there is no closed-app background sync.
  - A ride is synced before its parts.
  - On 403/404 the draft stays as "denied", with the server message and a "לנסות שוב" (retry) button. Drafts denied for reasons that can change (window closed, no access, signed out) are retried automatically when the journal is opened, so a draft refused before the manager reopened access goes through afterwards.
  - On 409 `version_conflict` the draft is merged safely (D-45); there is no blind "keep mine".
  - Network errors and gateway errors (502/503/504) leave the draft pending. A 401 also leaves it pending and asks the person to sign in again; it is never turned into "denied".
  - Progress changes only after the server confirms.
- **D-42 Notifications-off notice.** The persistent banner on the home screen shows unless browser permission is granted **and** the server reports a connected phone channel. No channel is connected yet, so it currently always shows. It links to instructions (iPhone home-screen install with iOS 16.4+, Android Chrome) and states plainly that phone delivery is not active yet. It never blocks registration.

## Offline reopening, duplicate rides and conflicts (before milestone D)

- **D-43 Duplicate-ride cancellation.** `POST /api/journal/rides/{id}/cancel` with `reason` (2–300 chars) and optional `duplicate_of_ride_id`; `POST …/restore` with a reason.
  - **Who:** the trainee's current home-day manager or an admin, the same authority as progress corrections (D-39). This is a reversible default: trainees and mentors cannot cancel today.
  - **Effect:** a soft cancellation. The row and its parts stay. The ride stops counting toward progress, completeness and reminders, disappears from mentors' views, and refuses description/assessment edits (409 `ride_cancelled`). An offline create retry with the same id returns the cancelled ride (`cancelled: true`) and never revives or recounts it.
  - **Visibility:** the trainee, home-day manager and admin see the cancelled ride with its reason and "duplicate of ride N".
  - **Integrity:** the DB check constraints require who/when/why together and allow `duplicate_of` only on a cancelled ride, never itself. The original must belong to the same trainee and not be cancelled. Repeating the request changes nothing (`already_cancelled`). Audit rows `ride_cancel`/`ride_restore` hold ids and seq only; the free-text reason stays on the ride.
  - This replaces fixing duplicates with a −1 progress correction (corrections remain for other cases). Migration `0003`.
- **D-44 Offline reopening.**
  - **Shell:** a service worker (`frontend/public/sw.js`, production builds only) caches the app shell: HTML plus the hashed JS/CSS, precached at install. It never caches `/api/*`.
  - **Identity:** the last confirmed `/api/auth/me` is kept in `localStorage` (`mda-last-me`). When the server is unreachable the app opens in offline mode with that identity. It grants nothing server-side. It expires after 30 days (the session idle limit) and is removed on logout or on any 401. Logout needs the server, because an offline logout could not revoke the session.
  - **Device copy:** the last journal view is kept per account in IndexedDB (`snapshots` store, DB version 2), bounded by the viewer's access:
    - Trainee's own journal: kept for 30 days.
    - Mentor: only the rides and summary of shifts in the current window, kept until the window or extension ends. The server returns `access.mentor_access_until`.
    - Managers and admins keep no copy.
    - Expired copies are deleted when read; drafts are never deleted with them.
  - **Offline screens:** journal and mentor pages work from the copy, with a banner saying it is a device copy and that the server re-checks permissions on sync. Every other page shows an offline home that lists the available copies and the pending-draft count. Every 30 s, and on the `online` event, the app checks the server again.
  - **Limits:** no closed-app background sync (Background Sync API not used). iPhone needs the home-screen install from milestone D for a durable service worker.
- **D-45 Safe conflict resolution.**
  - **Base:** every description/assessment/summary draft stores `base`, the server values it was edited from.
  - **Merge:** on 409 the server's `current` copy is merged with a three-way merge per field group (`frontend/src/merge.ts`): each marker, the case summary, the description, and strengths/improvements each together with its "no comments" flag. Edits to different fields combine automatically and are re-sent immediately on the server's version. Equal edits are not conflicts.
  - **Real conflicts:** the person sees "שלי" vs "שמור בשרת" for each conflicting field only and chooses; non-conflicting fields stay merged. If the server changes again meanwhile, the next 409 repeats the process.
  - **Unknown base:** old drafts, or a create retry where the server already holds a different description, make every differing field a conflict. Neither text is dropped.
  - **Tests:** `npm test` (Node's built-in runner).

## Morning registration and UI (2026-10-12)

- **D-46 Morning shifts hidden from registration on Sunday–Friday.** Adi's decision, 2026-10-12. It overrides the earlier special-day default for Friday, holiday and holiday-eve mornings.
  - A morning shift on Sunday–Friday is created with `self_selectable = false`, whatever the day kind, so creating it never opens it.
  - The day's managers or an admin open or close it with `POST /api/manage/shifts/{id}/registration {open}` (audited `morning_registration_open/close`). The manager board shows a "פתיחה להרשמה" / "הסתרה מההרשמה" button.
  - Saturday morning is always open (409 `saturday_fixed`).
  - Closing hides the shift from volunteer screens and refuses new answers. Existing answers remain in the manager's pool; assignments and history are untouched. Managers can still schedule mornings manually.
  - Migration `0004` applied this to existing dates, except mornings that already had registration answers, which stay open.
  - **Open:** a holiday or holiday eve on Sunday–Friday follows the weekday rule (hidden until opened). Confirm whether holidays should behave like Saturday.
- **D-47 UI pass 1** (volunteer home + registration only; awaiting review before the rest of the app):
  - **Home sections:** "נדרשת פעולה" (invitations and unanswered open dates, one card per date), "השיבוצים שלי" (published assignments with an ambulance chip), "הרשמה למשמרות" (status pill per date), then messages and quick links.
  - **Status cues:** status always has a text label, not colour alone.
  - **Logo:** the official logo is read from `frontend/public/brand/mda-logo.svg` (or `.png`). When the file is missing, nothing is shown in its place.

- **D-48 Ambulance capacity setting limited to 0–3** (Adi's decision, 2026-10-12).
  - **Validation:** the backend (`_check_capacity`, 400 `capacity_out_of_range` with a Hebrew message) and the manager board's selector both allow 0–3.
  - **Setting only:** the limit applies to the capacity setting, not the crew. Assigning more people than the capacity still succeeds with the existing warning, and publication is allowed.
  - **Older values above 3** are never changed automatically.
    - A new draft copies them unchanged, and the board shows "מעל 3 — לבדיקה" (above 3, for review).
    - A manager can lower them, but not save a value above 3 again.
    - `python -m app.cli report-capacity` lists every such slot (all versions) for review; it is read-only.
  - **Database:** there is no DB check constraint, because it would break copying older published values into drafts.

- **D-49 Journal save indicators** (2026-10-12). Saving happens only when a save button is pressed ("שמירת תיאור", "שמירת הערכה", "שמירת סיכום"), or immediately when "+ נסיעה …" is tapped (that tap creates the ride itself). There is no autosave while typing.
  - **Order:** each save is written to IndexedDB first and then sent to the server at once. Every part shows one state:
    - "שומר…" while sending.
    - "טיוטה במכשיר — ממתינה לסנכרון" when it is kept locally (offline or server unreachable).
    - "✓ נשמר במערכת" only after the server confirmed that exact draft (the key is returned by `syncDrafts`).
    - A red "השמירה במערכת נכשלה — הטיוטה נשמרה במכשיר" with the server's reason when it is refused.
  - **Limit:** text typed but not saved used to be lost. D-50 fixed this the next day.

- **D-50 Local typing drafts** (2026-10-13).
  - **Local only:** what is typed or selected (descriptions, markers and case summaries, shift summaries) is written to a separate IndexedDB store, `edits` (DB version 3), 400 ms after typing stops. It is never sent automatically.
  - **Explicit Save:** pressing Save is still the only way to queue a change for the server. Save cancels any pending local write first.
  - **States:** "שינויים שלא נשמרו — שמורים במכשיר בלבד" (typed, not saved; with "ביטול השינויים") → "טיוטה במכשיר — ממתינה לסנכרון" (saved, queued) → "שומר…" → "✓ נשמר במערכת" (server confirmed).
  - **"+ נסיעה"** creates only a local ride (state `local`): "נסיעה חדשה במכשיר — עוד לא נשמרה ולא נספרת", with "מחיקת הטיוטה". The ride is queued only when content is saved; Save is disabled while the local ride has no content.
  - **Persistence:** local edits survive refresh, closing the tab and reopening offline.
- **D-51 What counts as a ride** (Adi's decision, 2026-10-13).
  - **Counts:** a ride counts once it has content: a case description, or at least one selected marker, or a per-case summary. Full documentation is not required.
  - **Empty records:** these never count toward progress, completeness or reminders, and the journal labels them "רשומה ריקה — לא נספרת".
  - **Clearing content** makes a ride stop counting again.
  - **Retroactive:** applied to existing data. The local test data had one empty mentor-created record, which no longer counts.

## Not built yet

- **D:** PWA manifest and service worker, a real Web Push provider and an iPhone/Android trial, password recovery, a holiday data source, photos, backup/restore, deployment.
- A learning-tools entry button. (Duplicate rides: see D-43.)
- Generic admin editing of every table. Admin currently edits users, roles, levels, days, templates, any schedule (through the board) and any journal (through the journal API).
