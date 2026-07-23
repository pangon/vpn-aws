# Runbook 04 — Start a VPN session (recurring)

Creates an ephemeral exit node in a region of your choice and routes your
traffic through it.

**Time required:** ~2 minutes (of which ~60–90 seconds is instance boot).

## Prerequisites

- One-time setup completed (runbooks [01](01-tailscale-setup.md),
  [02](02-aws-setup.md), [03](03-install-and-configure.md)).
- The Tailscale client running and logged in on the device(s) you want to
  route through the VPN.

## Steps

### 1. Pick a region

```sh
./vpn.py regions
```

Any enabled AWS region works (e.g. `eu-central-1` Frankfurt, `eu-west-2`
London, `us-east-1` Virginia, `ap-northeast-1` Tokyo).

### 2. Create the exit node

```sh
./vpn.py up eu-central-1            # default TTL: 4h
./vpn.py up eu-central-1 --ttl 2h   # or set your own
```

Expected output: auth key minted → instance launched → "running" →
`Exit node 'vpn-aws-eu-central-1' is online in your tailnet.`

The TTL is a **self-destruct timeout**: the instance terminates itself when
it expires, even if you forget it or your machine dies. Pick a TTL a bit
longer than the session you plan.

### 3. Route your device through it

- **Windows:** Tailscale systray icon → **Exit nodes** →
  `vpn-aws-eu-central-1`.
- **macOS:** menu bar icon → **Exit nodes** → pick the node.
- **Linux:** `tailscale set --exit-node=vpn-aws-eu-central-1`
  (add `--exit-node-allow-lan-access` if you need to keep reaching your LAN).
- **iOS / Android:** Tailscale app → **Exit node** → pick the node.

You can route any number of your devices through the same node, and each
device chooses independently.

## Verification

- Your public IP now geolocates to the chosen region:
  `curl -4 ifconfig.me` (or open it in a browser), then look it up on any
  IP-geolocation site — it should be an Amazon address in that region.
- `./vpn.py status` shows the instance `running` and the tailnet device
  online.

## Notes

- **One node per region** at a time; nodes in *different* regions can
  coexist and appear side by side in the exit node list.
- Throughput is modest on the default `t4g.nano` — fine for browsing; set a
  larger `instance_type` in the config if you need more.
- Each session gets a fresh public IP from Amazon's pool in that region.
- Expect streaming services to block or degrade on datacenter IPs — this is
  a documented non-goal (see [DECISIONS.md](../DECISIONS.md)).
