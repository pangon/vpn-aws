# Runbook 01 — Tailscale setup (one-time)

Prepares your tailnet so ephemeral exit nodes can join it automatically.
You do this once; nothing here needs to be repeated per session.

**Time required:** ~15 minutes.

## Prerequisites

- None. You will create the Tailscale account in step 1 if you don't have one.

## Steps

### 1. Create your tailnet

Sign up at <https://login.tailscale.com/start> using an identity provider you
already have (Google, GitHub, Microsoft, Apple, or a passkey). The free
**Personal** plan is sufficient for this project.

### 2. Install the Tailscale client on your devices

Install from <https://tailscale.com/download> on every device you want to use
the VPN from (desktop and/or phone) and log in with the same identity.

> **WSL2 users:** install the Windows client on the Windows host, not inside
> WSL. The exit node applies to the whole machine, including WSL traffic.

### 3. Add the tag and auto-approval to your tailnet policy

Open the [admin console → Access controls](https://login.tailscale.com/admin/acls)
and add these two sections to the policy file (keep the rest of the default
policy as is):

```jsonc
{
    // Declare the tag used by the exit nodes; admins may assign it.
    "tagOwners": {
        "tag:vpn-aws": ["autogroup:admin"],
    },

    // Nodes joining with this tag are approved as exit nodes automatically,
    // with no manual step in the admin console.
    "autoApprovers": {
        "exitNode": ["tag:vpn-aws"],
    },
}
```

Optional — allow Tailscale SSH into the exit nodes for emergency debugging
(the instances have no key pair and no open inbound ports; this is the only
way in):

```jsonc
    "ssh": [
        {
            "action": "accept",
            "src":    ["autogroup:member"],
            "dst":    ["tag:vpn-aws"],
            "users":  ["root", "ec2-user"],
        },
    ],
```

Save the policy.

### 4. Create the OAuth client

The CLI uses an OAuth client to mint a short-lived, single-use auth key for
each session.

1. Go to [admin console → Settings → OAuth clients](https://login.tailscale.com/admin/settings/oauth).
2. **Generate OAuth client** with:
   - **Scopes:** `Keys` → `Auth Keys` → **Write** (nothing else).
   - **Tags:** `tag:vpn-aws` (the keys this client mints can only create
     devices with this tag).
3. Copy the **client ID** and **client secret**. The secret is shown only
   once — you will paste both into the CLI config in
   [runbook 03](03-install-and-configure.md).

## Verification

- The policy file saves without errors and contains `tag:vpn-aws` in both
  `tagOwners` and `autoApprovers.exitNode`.
- The OAuth client appears in the OAuth clients list with the `auth_keys`
  scope and the `tag:vpn-aws` tag.

Full end-to-end verification happens the first time you run
[runbook 04 — start a session](04-start-session.md).

## Notes

- If you prefer a different tag name, use it consistently in three places:
  the policy file, the OAuth client, and `tailscale.tag` in the CLI config.
- The OAuth secret can create pre-authorized devices in your tailnet: treat
  it like a password. It goes only into `~/.config/vpn-aws/config.toml`
  (chmod 600), never into the repository.
