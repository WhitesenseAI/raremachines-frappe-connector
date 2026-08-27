# Changelog

All notable changes to the `raremachines` Frappe app are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added

- **`api/intake_config.py`: `sync_lead_fields`** — a new, authenticated
  (`_verify_install_signature()`, same HMAC-over-`timestamp.body` scheme as
  every other RareMachine↔Conduit relay call) endpoint that creates/updates
  `CRM Lead` Custom Fields from a payload Conduit's `/ops` dashboard now
  owns and pushes, instead of a fixed list this app used to hardcode.
  Idempotent (`create_custom_fields`, same helper `install.py` already
  used) and **never deletes** a field — removing it from Conduit's config
  and re-syncing just stops managing that field, existing Lead data is
  left alone.

### Changed

- **`install.py`'s `_ensure_whatsapp_intake_customizations` no longer
  hardcodes Nest Healthcare's Lead field shape.** Every client who enabled
  `whatsapp_intake_enabled` used to get the exact same
  `enquiry_type`/`market_type`/`regulated_status`/`certificate` fields —
  now the installer only creates the FIXED infra fields every client needs
  regardless of business shape (section/column layout, `brochure_type`,
  `brochure_pdf`, the sent/timestamp bookkeeping fields). Business fields
  are pushed per-client via `sync_lead_fields` above. **Operational note:**
  a site that was already running the old hardcoded fields (Nest
  Healthcare) keeps them — nothing is deleted by this change — but a fresh
  site now needs one `sync_lead_fields` call (from Conduit's `/ops`, or a
  one-off script) before the guided-intake engine can write those fields to
  a Lead.

### Fixed

- **Stale `raremachines-settings` URL slug left over from the `RareMachines
  Settings` → `RareMachine Settings` DocType rename (commit `598c858`).**
  The rename updated every code reference to the DocType's *name*, but three
  hardcoded route strings and two test fixture paths still spelled out the
  pre-rename slug/folder name literally:
  - `raremachines/api/connect.py` — `settings_path` in `finish_browser_pair`
    and `finish_from_web`, and the "Cancel" link in the pairing consent
    page, all pointed at `/app/raremachines-settings`. Symptom: after a
    successful site pairing, the browser landed on a Frappe 404
    ("Page raremachines-settings not found") instead of the real settings
    page.
  - `raremachines/raremachines/doctype/raremachine_settings/raremachine_settings.js`
    — two `window.history.replaceState(...)` calls (clearing the
    `?paired=1`/`?pair_error=...` query string after showing the result
    banner) rewrote the URL bar to the same stale slug.
  - `raremachines/tests/test_security.py` — `TestPairErrorAllowlist.setUp`
    and `TestSettingsDoctypePermissions.test_only_system_manager_has_access`
    read fixture files from a `raremachines_settings/` folder that no
    longer exists post-rename, failing with `FileNotFoundError` on every
    run. This was the actual cause of 4 tests failing on every `run-tests`
    invocation since the rename — previously (incorrectly) assumed to be a
    pre-existing, unrelated issue and left unfixed for several sessions.
    Confirmed by the fix: `bench run-tests --app raremachines` went from
    52/56 to a clean 56/56 with no other changes.
  - All five references corrected to `raremachine-settings`/`raremachine_settings`
    (singular). No behavioural change beyond the URL/path strings —
    `frappe.utils.get_url_to_form("RareMachine Settings")` was already
    resolving correctly to the singular form; only these five hardcoded
    literals had drifted.

- **`_safe_return_to()` silently dropping the browser back onto Frappe's own
  Settings page instead of round-tripping to RareMachine (Conduit).** Not a
  code bug — `finish_from_web`'s `return_to` safety check
  (`raremachines/api/connect.py`) correctly refuses to redirect anywhere
  whose `(scheme, host)` doesn't match this site's own configured
  `raremachines_base_url`/`conduit_base_url` (`raremachines/config/saas.py`'s
  `get_conduit_base_url()`) — that's the intended anti-open-redirect
  behaviour, working as designed. The actual problem was operational: this
  site's `site_config.json` still had the pre-rename key
  (`conduit_base_url: "http://localhost:3000"`) from an earlier local dev
  session, so a real pairing round-trip from RareMachine's current public
  URL never matched, and the safety check correctly (if unhelpfully, from
  the outside) fell back to landing on this site's own Settings page
  instead. Fixed by setting the current, preferred key:
  `bench set-config raremachines_base_url "<current RareMachine base URL>"`
  — no code change needed. **Operational note, not a one-time fix**: this
  value has to be kept in sync with wherever RareMachine is actually
  reachable from (e.g. a new ngrok URL after a tunnel restart) or the same
  symptom recurs.

- **Frappe OAuth Client's registered `redirect_uris` going stale after
  RareMachine's own public URL changed**, causing Frappe's own
  `frappe.integrations.oauth2.authorize` endpoint to reject the request
  outright with `{"error": "invalid_request", "description": "Mismatching
  redirect URI."}` before this app's own code ever ran. The `OAuth Client`
  doc minted at initial site-pairing time hard-codes the callback URL that
  was current at that moment (`http://localhost:3000/api/integrations/frappe/callback`
  from an earlier local session); Frappe's own OAuth2 provider validates the
  live `redirect_uri` query parameter against this stored value on every
  authorize request, with no fallback. Also not a code bug — fixed by
  updating the existing `OAuth Client` record's `redirect_uris` field to the
  current callback URL. **Same operational-drift class as the
  `raremachines_base_url` issue above**: whenever RareMachine's public URL
  changes, both this doc and `raremachines_base_url` need updating together,
  or pairing breaks in two different ways depending on which flow is
  exercised first.
