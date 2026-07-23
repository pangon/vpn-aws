# Runbook 05 — End a VPN session (recurring)

Tears down the exit node(s). After this, the AWS account is back to zero
running resources and zero standing cost.

**Time required:** ~1 minute.

## Steps

### 1. Deselect the exit node on your devices — first

Systray/app → **Exit nodes** → **None** (Linux: `tailscale set --exit-node=`).

Do this *before* terminating: a device still pointed at a dead exit node
keeps trying to route through it, and its traffic stalls until you deselect.
Nothing breaks permanently — but you'll wonder why the internet is down.

### 2. Terminate the node(s)

```sh
./vpn.py down                 # everything, in every region
./vpn.py down eu-central-1    # or just one region
```

Termination is asynchronous; the instance goes `shutting-down` →
`terminated` within a minute or two. The root volume is destroyed with the
instance and no Elastic IP was ever allocated, so nothing survives.

If you forget this step entirely, the TTL watchdog terminates the instance
anyway when it expires.

## Verification

```sh
./vpn.py status
```

Expect `No vpn-aws instances in any region.` (a just-terminated instance may
still show as `shutting-down` for a minute — that's fine and costs nothing).

The tailnet device entry is **ephemeral**: it disappears from the device
list (and from the exit node picker) by itself shortly after going offline.
No manual cleanup needed; if it lingers a while, it is inert and harmless.

## Notes

- `down` with no region scans every enabled region, so it also catches nodes
  you forgot about in other regions.
- For a periodic paranoia check on leftovers and costs, see
  [runbook 06](06-troubleshooting.md#leftover-audit).
