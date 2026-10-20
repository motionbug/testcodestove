# Duepi / MyDPremote local protocol notes

These are interoperability observations, not a vendor specification. They describe
the implementation and one reference capture; unknown firmware variants and the
live reliability issues in [KNOWN_ISSUES.md](KNOWN_ISSUES.md) remain unresolved.
No vendor binary, original program source, or private traffic is distributed.

## Connection model

The existing module connects outbound to a vendor TCP relay on port 3000.
Redirecting its DNS to the local bridge allows a local TCP server to exchange
commands over that module connection. The bridge then publishes a Home Assistant
MQTT climate entity. The standalone controller offers a localhost HTTP UI instead.
App-side reference traffic uses `master:<device-code>#`; this bridge rejects app
clients and accepts only a configured stove peer IP.

## Key derivation and frame codec

`key = MD5(UTF8(device_code_without_trailing_hash)) XOR mask`

The 16-byte mask is `23FADC18A422678312C86AC29281AC01`. It is a shared
interoperability constant, not a complete device AES key. Each installation
must supply its own private device code; never publish a device code or actual
resulting key. Fixed vectors in the tests use a synthetic code.

The inner command is six ASCII bytes followed by a two-character uppercase
hex checksum: the sum of those six ASCII bytes modulo 256. The eight-byte
payload is zero-padded to 16 bytes and encrypted using AES-128-CBC with a fresh
16-byte IV. The wire frame is ESC + Base64(IV || ciphertext) + `&`, 46 bytes in
the captured encrypted format. Captured replies contain an eight-character
payload/checksum, then NUL and uninitialized device memory; the decoder ignores
bytes after that first NUL. Requiring all-zero reply padding breaks that evidence.

## Commands used by this bridge

| Command | Interpreted meaning |
|---|---|
| `RD9000` | Phase/status |
| `RD1000` | Ambient temperature, first four hex digits divided by ten |
| `RC6000` | Requested temperature, first four hex digits |
| `RC5000` | Power setting, first four hex digits |
| `RDA000` | Error code |
| `RF0000` | Request shutdown |
| `RF0010`–`RF0050` | Request power P1–P5 |
| `RF2xx0` | Target temperature, `xx` is hex degrees Celsius, 16–35 |

The implementation labels phases 0x01/0x02 as startup, 0x04 as running,
0x40 as eco/idle, and other supported states as off/cooling/cleaning/alarm.
Command acceptance and state interpretation still need validation per model.
A checksum-valid ACK is not proof the physical appliance applied a setting.

## Transport safeguards

TCP buffering preserves fragments and coalesced frames. Commands are serialized
and tied to a connection. Invalid encodings/checksums cannot update state.
A reply timeout invalidates the session so a later reply cannot be matched to
a different command. The default timeout is four seconds. Current ten-byte
unsolicited frames are not understood or treated as command replies.
