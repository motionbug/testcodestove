# Security and private configuration

Never publish device codes, derived AES keys, MQTT passwords, options.json,
raw logs, or packet captures. The codec's shared derivation mask and synthetic
test vectors are public interoperability material, not real device credentials.
Each user supplies their own device code; see the add-on guide's
[find your device code](duepi_stove_addon/DOCS.md#1a-find-your-device-code) and
[what is safe to share](duepi_stove_addon/DOCS.md#1b-what-is-safe-to-share) sections.
A device code works like a password for the vendor relay: if yours leaks, contact
Duepi or your stove dealer, because this project cannot revoke it.

Keep TCP port 3000 on a trusted LAN. The peer-IP allowlist is not cryptographic
authentication. The standalone HTTP UI binds to localhost. MQTT TLS and retained
command replay protection are not implemented; never retain command messages.

Do not put sensitive material in a public issue. Use the repository's private
security reporting feature if available; otherwise ask for a private reporting
channel without including the sensitive details.
