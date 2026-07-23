# Runbook 03 — Install and configure the CLI (one-time)

Sets up the `vpn.py` CLI on the machine you will orchestrate sessions from.

**Time required:** ~10 minutes.

## Prerequisites

- [Runbook 01 — Tailscale setup](01-tailscale-setup.md) completed (you have
  the OAuth client ID and secret).
- [Runbook 02 — AWS setup](02-aws-setup.md) completed (the `vpn-aws` AWS
  profile works).

## Steps

### 1. Install uv

The CLI is a self-contained Python script run by [uv](https://docs.astral.sh/uv/),
which provides the Python runtime and dependencies automatically:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Get the repository

```sh
git clone https://github.com/pangon/vpn-aws.git
cd vpn-aws
./vpn.py --help   # first run downloads dependencies; must print usage
```

### 3. Create the configuration file

```sh
mkdir -p ~/.config/vpn-aws
cp config.example.toml ~/.config/vpn-aws/config.toml
chmod 600 ~/.config/vpn-aws/config.toml
"${EDITOR:-nano}" ~/.config/vpn-aws/config.toml
```

Fill in:

- `tailscale.oauth_client_id` / `oauth_client_secret` — from runbook 01,
  step 4.
- `aws.profile = "vpn-aws"` — the profile from runbook 02 (uncomment the
  line).

Leave the rest at defaults unless you changed the tag name in runbook 01.

## Verification

```sh
./vpn.py regions   # exercises the AWS credentials: prints the region list
./vpn.py status    # exercises both AWS and the Tailscale API:
                   # expect "No vpn-aws instances in any region." and
                   # "No tailnet devices tagged tag:vpn-aws."
```

If both commands run cleanly, everything is wired up. Continue with
[runbook 04 — start a session](04-start-session.md).

## Notes

- The config file contains the Tailscale OAuth secret: keep it `chmod 600`
  and out of any repository or backup you don't trust.
- The CLI works fine from WSL2 — it only talks to AWS and Tailscale APIs.
  It is the *Tailscale client* that must run on the Windows host
  (runbook 01, step 2).
