# Security and private configuration

Never publish device codes, derived AES keys, MQTT passwords, options.json,
raw logs, or packet captures. The codec's shared derivation mask and synthetic
test vectors are public interoperability material, not real device credentials.

Keep TCP port 3000 on a trusted LAN. The peer-IP allowlist is not cryptographic
authentication. The standalone HTTP UI binds to localhost. MQTT TLS and retained
command replay protection are not implemented; never retain command messages.

Do not put sensitive material in a public issue. Use the repository's private
security reporting feature if available; otherwise ask for a private reporting
channel without including the sensitive details.
