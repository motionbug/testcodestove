# UniFi DNS redirection

First verify the Home Assistant app starts and publishes MQTT discovery.
Reserve the Home Assistant and stove addresses in DHCP where practical.

Create two standalone **Host (A)** records, pointing to the **Home Assistant
host's LAN IPv4 address**, not the stove address:

- `duepiwebserver.com`
- `duepiwebserver1.com` (include when used by your module; not independently
  confirmed for every firmware)

Ubiquiti's documented paths:

- Network 9.4: Settings → Policy Table → Create New Policy → DNS.
- Network 9.3: Settings → Policy Engine → DNS → Create DNS Record.

Ensure DHCP for the stove's VLAN supplies the gateway as its DNS server.
Public resolvers bypass gateway-local records. These overrides affect clients
using that resolver, not only the stove. Validate from the stove's network:

```sh
dig @GATEWAY_IP duepiwebserver.com A +short
dig @GATEWAY_IP duepiwebserver1.com A +short
```

Both answers should be the Home Assistant IP. Then reconnect the module using
its supported procedure, without interrupting the stove's required shutdown or
cooling sequence. Confirm `Stove TCP connection established` in the bridge log.
For separate VLANs, permit only stove-IP → Home-Assistant-IP TCP 3000 as needed.
DNS changes do not move an already-established TCP connection.

Do not create a WAN port-forward. DNAT interception is a conditional fallback;
LAN-ingress support and return routing depend on the gateway and topology.
Source NAT can conflict with the bridge's original-stove-IP allowlist. Same-subnet
DNAT may produce asymmetric replies. Investigate those details before adding NAT.

Official references:
- https://help.ui.com/hc/en-us/articles/15179064940439-UniFi-DNS-Records-and-Local-Hostnames
- https://help.ui.com/hc/en-us/articles/16437942532759-DNAT-SNAT-and-Masquerading-in-UniFi
