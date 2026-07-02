# LocalSend Receiver — Design

**Date:** 2026-07-02
**Decision (user):** strict LocalSend v2 protocol only — official LocalSend
clients are the senders; no custom CLI, no protocol extensions. Destination
folder selection, overwrite negotiation, and pairing secrets are out of scope.

## Goal

Other devices running LocalSend can discover this device and push files
directly into MDRender's encrypted store. Receive-only.

## Protocol (LocalSend v2, HTTP)

- **Port:** 53317 preferred; falls back to 53318–53327 when taken (e.g. the
  official LocalSend app on the same phone). Announcements carry the actual
  port.
- **Discovery:** UDP multicast `224.0.0.167:53317` — announces on start,
  answers `announce: true` packets via unicast + multicast. `POST/GET
  /api/localsend/v2/register` and `GET /api/localsend/v2/info` also served.
- **Transfer:** `POST prepare-upload` (blocks until user decision; 401 wrong
  PIN, 403 rejected/timeout, 409 busy) → `POST upload?sessionId&fileId&token`
  per file → optional `POST cancel`.
- Announced as `protocol: "http"` — no TLS in v1 (senders honor the announced
  protocol).

## Components

- `LocalSendPrefs` — enabled flag, random alias ("Nimble Cactus"), optional
  PIN, persistent random fingerprint.
- `LocalSendServer` — NanoHTTPD; protocol endpoints.
- `LocalSendDiscovery` — multicast announce/respond.
- `LocalSendSessionManager` — single active session; parks prepare-upload on a
  `CompletableDeferred` (60 s timeout → reject); token validation; imports via
  `FileRepository.importFile` into an auto-created **LocalSend** folder with
  `name (1).ext` auto-rename on conflicts.
- `LocalSendService` — foreground service (`dataSync`), multicast lock,
  persistent notification showing the alias; transfer-request notification
  with Accept/Reject actions when the app is backgrounded; completion notice.
- MainActivity — in-app Accept/Reject dialog when foregrounded; completion
  toast.
- Settings — enable switch (starts/stops service, requests POST_NOTIFICATIONS
  on 13+), alias display/regenerate, optional transfer PIN.

## Verified (on device, 2026-07-02)

`info` + `register` endpoints, discovery startup, prepare-upload accept flow,
upload → encrypted storage in auto-created LocalSend folder, port conflict
fallback with the official LocalSend app installed on the same phone.

## Known limitations

- Receive-only (`download: false`); no TLS; single session at a time.
- Large files bounded by the Room-BLOB storage architecture (same as import).
