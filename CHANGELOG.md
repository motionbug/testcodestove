# Changelog

## 1.0.5 — 2026-11-09

- Replace stale TCP sessions immediately when a new connection arrives from the
  configured `stove_ip` (`SO_KEEPALIVE` and `TCP_NODELAY` enabled) and wake the
  poll loop on reconnect, in both the Home Assistant bridge and `dpremote/stove.js`.
- Hold MQTT availability as `online` across brief TCP reconnects within a
  20-second grace window (`availability_grace`, default `20.0`s) before expiring
  to `offline`.
- Pace sequential stove exchanges by `150ms` (`command_delay`, default `0.15`s)
  to avoid back-to-back serial bursts during 5-command status polls.
- Hold and deliver user MQTT commands (`mode`, `fan_mode`, `target_temperature`)
  across poll timeouts and brief TCP reconnects within the grace window, with a
  single retry if a socket is replaced mid-command and generation-based
  coalescing of superseded intents.
- Decouple the post-command verification poll from command acknowledgement so a
  transient readback timeout never erases `pending_power` or `acknowledged`
  status, and allow one guarded resend on persistent readback mismatch
  (`mismatch_polls >= 2`) in phases `0x02` and `0x04`.
- Demote unsolicited 10-byte idle frames (`\x1b00000000&` when no command reply
  is pending) from warning to debug in Python and ignore them quietly in Node.
- Add regressions: baseline now 19 Node tests and 40 Python tests, exercised
  with Paho 1.6.1 and 2.1.0. Live hardware validation of 1.0.5 remains pending.

## 1.0.4 — 2026-11-02

- Log one warning per connection when three replies fail the checksum before any
  valid reply. This almost always means a wrong device code, which previously
  appeared only as poll timeouts. The warning never includes the code, key, or
  frame contents. Same diagnostic in the standalone Node controller.
- Document how to find your device code by capturing your own MyDPremote app's
  `master:<code>#` login (iPhone, Android, or router), and what is safe to share.
- Add regressions: baseline now 19 Node tests and 32 Python tests, with Paho
  1.6.1 and 2.1.0 coverage. Protocol, timeouts, and power handling are unchanged.

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
