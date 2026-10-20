# Changelog

## 1.0.3 — 2026-10-20

- Separate requested power from observed readback; command ACK no longer publishes
  an unconfirmed fan setting.
- Serialize complete polling and command verification; coalesce concurrent polls
  and superseded queued power commands.
- Permit one guarded same-session power resend on observed startup-to-running
  transition, with 15-minute expiry and cancellation on Off, alarm, disconnect,
  or newer intent. Preserve pending power on repeated Heat; discard offline requests.
- Add regressions: baseline now 17 Node tests and 29 Python tests, with Paho
  1.6.1 and 2.1.0 coverage. Startup recovery remains unverified on hardware.
- Publish sanitized source, screenshots, install/protocol/security guidance,
  and full MIT acknowledgement of Florian Casse's DuepiRemoteHA.

### Maintenance documentation

- Add architecture/command-flow diagrams, safe update/export/publishing procedure,
  and a fresh-session contributor entry point. No runtime changes or version bump.

## 1.0.2

- Add power-command/reply/readback and malformed-frame size diagnostics without
  exposing device codes, keys, or raw frames. Power/timeouts remained unresolved.

## 1.0.1

- Support MQTT client startup and connect callbacks with both Paho 1.x and 2.x.

## Initial implementation

- Local AES codec, peer-IP filtering, stream buffering, serialized exchanges,
  timeout session closure, standalone controller, and MQTT climate discovery.
