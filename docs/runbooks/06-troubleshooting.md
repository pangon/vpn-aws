# Runbook 06 — Troubleshooting and leftover audit

Consult when something misbehaves, and run the [audit](#leftover-audit)
whenever you want to confirm the account is truly at zero.

## `up` times out: the node never joins the tailnet

The instance launched but `vpn.py` gave up waiting for the device to appear.

1. Check the instance state: `./vpn.py status`. If it is `running`, the boot
   script likely failed; if it is already `terminated`, look at AWS-side
   errors first.
2. Read the boot log (works ~1–2 minutes after boot):

   ```sh
   aws ec2 get-console-output --profile vpn-aws --region <region> \
     --instance-id <id> --latest --output text | tail -50
   ```

3. Common causes:
   - **Tag mismatch** — the tag in the tailnet policy (`tagOwners`,
     `autoApprovers`), on the OAuth client, and in `config.toml` must be
     identical. A mismatch makes `tailscale up` fail with a 4xx error
     visible in the console output.
   - **Expired session key** — the minted auth key lives 15 minutes; if the
     instance boots extremely slowly (rare), the join fails. Just `down`
     and retry.
   - **Transient package/repo failure** — retry; the watchdog will destroy
     the failed instance at TTL anyway, but `./vpn.py down <region>` is
     immediate.
4. Whatever the cause: `./vpn.py down <region>` cleans up, and the run costs
   were less than a cent.

## `up` fails with "no default VPC"

The region's default VPC was deleted at some point. Recreate it once with an
admin identity (this is outside the CLI's minimal permissions):

```sh
aws ec2 create-default-vpc --region <region>
```

Default VPCs are free; this restores the standard AWS baseline for the
region.

## `up` fails with capacity/instance-type errors

`InsufficientInstanceCapacity` or `Unsupported` can occur if the default
subnet's availability zone momentarily lacks `t4g.nano`. Retry, or pick
another region; if it persists, set a different `instance_type` in the
config (any `t4g.*` size).

## Exit node selected but no internet

- Confirm the node is online: `./vpn.py status` (instance `running`, device
  recently seen).
- Deselect and reselect the exit node in the client.
- If the node was terminated while selected, deselect it (see
  [runbook 05](05-end-session.md), step 1).
- Check that your client's Tailscale version is reasonably current.

## Throughput is poor

`t4g.nano` is deliberately the cheapest choice and is fine for browsing.
For sustained heavy transfer set `instance_type = "t4g.small"` (or larger)
in the config — remember egress ($0.09/GB) will dominate the bill long
before the instance size does.

## Emergency access to the instance

There is no SSH key pair and no open inbound port, by design. If you added
the optional `ssh` stanza in [runbook 01](01-tailscale-setup.md), you can
use Tailscale SSH while the node is up:

```sh
tailscale ssh ec2-user@vpn-aws-<region>
```

Otherwise, the console output (above) is the debugging tool; when in doubt,
`down` and recreate — sessions are disposable by design.

## Leftover audit

The design creates **only tagged EC2 instances** — no key pairs, no Elastic
IPs, no security groups, no snapshots. So auditing is one command:

```sh
./vpn.py status        # scans every enabled region for the Project=vpn-aws tag
```

`No vpn-aws instances in any region.` means zero standing cost from this
tool.

For belt-and-braces (e.g. after experimenting manually):

- **Billing → Bills / Cost Explorer** in the console: EC2 charges should be
  cents and only in months you used the VPN.
- The budget alarm from [runbook 02](02-aws-setup.md#4-recommended-create-a-billing-guard)
  is the always-on tripwire.

On the Tailscale side, ephemeral devices remove themselves; anything stale
in the [admin console machines list](https://login.tailscale.com/admin/machines)
can be deleted by hand and is inert regardless.
