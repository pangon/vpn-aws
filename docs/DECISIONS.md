# Design Decisions

- **Status:** accepted
- **Date:** 2026-07-23
- **Deciders:** project owner

This document records the decision process that shaped this project: the goals, the
alternatives that were evaluated, why each was kept or discarded, and the solution that
was finally chosen. It is intentionally more discursive than a README — it explains the
*why*, not the *how to use it*.

## 1. Context and goals

The goal is a **personal VPN** with the following characteristics:

- Deployable on a **personal AWS account**, owned and operated by the user.
- Activated **on demand** from a personal computer via a simple procedure (script/CLI),
  with the ability to **select a geographic region** for the VPN exit point.
- **Open source**, so others can reuse it.

The expected usage pattern drives most of the design:

- **Very occasional use**: roughly a couple of hours per week (~8–10 hours/month).
- Therefore a strong preference for **pay-per-use over flat pricing**: the
  infrastructure should be **created when needed and destroyed afterwards**, with zero
  (or near-zero) standing costs while idle.

### Non-goals

- **Anonymity.** A single-user VPN on a personal cloud account shifts trust from the
  local ISP to AWS; it does not hide identity. Traffic egresses from an IP tied to the
  owner's AWS account.
- **Unblocking streaming services.** AWS IP ranges are well-known datacenter addresses;
  streaming platforms commonly block or penalize them. IP *geolocation* works fine
  (traffic appears to originate from the chosen region), but geo-restricted streaming is
  explicitly out of scope — a commercial VPN serves that use case better.
- **Multi-tenant / team use.** This is a personal tool.

## 2. Key constraints discovered during evaluation

Two facts about AWS pricing shaped the evaluation more than anything else:

1. **Egress traffic, not compute, dominates cost.** A `t4g.nano` instance costs
   ~$0.0042/hour, but data transfer out of EC2 costs ~$0.09/GB. For an always-on VPN
   handling 100 GB/month, bandwidth would cost ~3× the instance itself.
2. **Lightsail's bundled transfer allowance is prorated.** Lightsail plans nominally
   include 1–2 TB/month of transfer for $3.50–5/month, which looks ideal for a VPN —
   but the allowance is prorated over the hours the instance actually exists. An
   instance alive for a 2-hour session gets only ~5–6 GB of included transfer, after
   which the usual $0.09/GB applies. With an ephemeral usage pattern, Lightsail loses
   its main advantage over EC2, while offering fewer regions and a less convenient API.

Conclusion: for this usage pattern, **ephemeral EC2 instances are the right substrate**
(~$0.01 of compute per 2-hour session; egress is the only significant cost; realistic
total of $1–3/month, and exactly $0 when idle).

## 3. Alternatives considered

### 3.1 AWS Client VPN (managed service) — rejected

AWS's managed VPN endpoint service.

- ~$0.10/h per subnet association + $0.05/h per connection. Always-on it costs
  ~$75+/month, which is absurd for personal use. Used on-demand it drops to
  ~$1.50/month for 10 hours — acceptable in absolute terms, but still ~30× the hourly
  cost of a nano instance.
- Endpoint association takes ~10 minutes, making on-demand activation slow.
- Uses OpenVPN rather than WireGuard.
- Designed for fleet access into a VPC, not for internet egress through a region.

**Verdict:** wrong tool for this job, at the wrong price.

### 3.2 Algo VPN (Trail of Bits) — rejected for this pattern

Mature, actively maintained Ansible project that provisions a hardened WireGuard/IPsec
server on many cloud providers, including EC2 and Lightsail.

- Excellent for its intended model: **deploy once, keep running**.
- Poor fit for ephemeral use: every deploy is a multi-minute Ansible run that
  regenerates keys and client configs, which must then be re-imported into the client
  at every session.
- No dynamic "spin up in region X now, tear down after" experience.

**Verdict:** the best ready-made option for a *persistent* personal VPN, but it fights
the ephemeral pay-per-use pattern rather than supporting it.

### 3.3 Outline (Outline Foundation, formerly Jigsaw) — rejected

Shadowsocks-based proxy with a friendly management GUI, transitioned to the independent
Outline Foundation in 2026.

- Built primarily for censorship circumvention, not as a general-purpose system VPN.
- The Manager assumes a persistent server; recreating the server each session means new
  access keys to redistribute every time.

**Verdict:** solves a different problem (anti-censorship, easy key sharing with others).

### 3.4 Commercial VPN providers — rejected by project premise

Mullvad and similar providers are cheaper per month than any self-hosted option once
egress is accounted for, offer far more exit locations, and are the only realistic
option for streaming geo-unblocking (see non-goals).

**Verdict:** legitimate alternative for many use cases, but the point of this project
is a self-hosted, self-controlled VPN on a personal AWS account.

### 3.5 DIY: ephemeral EC2 + WireGuard + custom CLI — serious candidate, not chosen

The original idea behind this repository: a CLI that provisions an ephemeral EC2
instance running WireGuard in a chosen region (`vpn up eu-central-1`), generates and
exchanges keys, activates the tunnel locally, and destroys everything on disconnect
(`vpn down`).

Strengths:

- **Full independence**: no third-party service, no account other than AWS, plain
  WireGuard end to end.
- True pay-per-use with zero standing cost.
- As an open source project, it fills a real gap: existing Terraform modules and blog
  posts cover pieces of this, but no polished "one command, pick a region, ephemeral
  lifecycle" tool was found.

Weaknesses:

- Substantially more code to write and maintain: WireGuard key generation and exchange,
  client config templating, handling the new server IP at every session, activating the
  profile on each client OS (including the Windows/WSL2 split on the primary client
  machine).
- Multi-device (e.g. phone) support requires meaningful extra work.
- All of the security surface (key handling, open UDP port, hardening) is owned by this
  project.

**Verdict:** the strongest alternative. Rejected in favor of Tailscale mainly on
effort-to-value: most of what this project would implement is undifferentiated plumbing
that Tailscale already does better.

### 3.6 Tailscale + ephemeral exit nodes — **chosen**

Tailscale is a mesh VPN built on WireGuard. A SaaS control plane handles identity
(SSO), public key distribution, ACLs and NAT traversal; data traffic flows directly
peer-to-peer over WireGuard (private keys never leave the devices; Tailscale cannot see
traffic contents, though it does see metadata: devices, connection times, IPs).

A node in the tailnet can advertise itself as an **exit node**, routing all internet
traffic of other nodes — exactly the role of a VPN server in a chosen region.

How it fits this project:

- **Per-session flow:** a small orchestrator launches an EC2 instance in the chosen
  region; cloud-init installs Tailscale and runs `tailscale up` with an **ephemeral,
  pre-authorized, tagged auth key** and `--advertise-exit-node`. An `autoApprovers`
  rule in the tailnet policy approves the exit node automatically. ~60–90 seconds
  later the node appears in the tailnet; the user selects it as exit node (systray on
  Windows, one command on Linux, same list on iOS/Android). On teardown the instance is
  terminated and the ephemeral node evaporates from the tailnet on its own.
- **Cost:** identical to the DIY option (the EC2 instance is the only cost).
- **Client UX:** better than anything this project would realistically build —
  first-class GUI clients on every OS, mobile included, with no per-session config
  distribution.
- **Security posture:** the instance's security group can be fully closed to inbound
  traffic (the node only makes outbound connections; NAT traversal handles the rest).
  No key management owned by this project at all.
- **Free tier:** the Personal plan (free, declared "free forever") covers 6 users and
  unlimited personal devices; exit nodes are included. As of the April 2026 pricing
  update this is more than sufficient for personal use.
- **Development effort:** the project reduces to a thin orchestrator — EC2
  `RunInstances`/`TerminateInstances`, one Tailscale API call to mint the ephemeral
  auth key (via an OAuth client), and optional convenience glue (wait-until-online,
  auto-select exit node).

## 4. Decision

**Use Tailscale with ephemeral EC2 exit nodes.** This repository will contain:

1. An orchestrator (script/CLI) that creates and destroys single ephemeral EC2
   instances in a user-chosen AWS region, configured via cloud-init to join the
   user's tailnet as an auto-approved ephemeral exit node.
2. The minimal Tailscale-side setup documentation (tag, `autoApprovers` policy, OAuth
   client for auth key generation).
3. Cost and security notes for users deploying it on their own AWS accounts.

The decisive arguments were:

- **Effort-to-value:** roughly a weekend of work versus weeks for the DIY option, with
  a better end-user experience (especially on mobile).
- **The repo is a means, not an end:** the primary goal is a working personal VPN, not
  building a WireGuard management layer.
- **Cheap reversibility** (see below).

## 5. Consequences and accepted trade-offs

- **SaaS dependency:** account, ACLs and coordination live on Tailscale's control
  plane. If it is unreachable, new sessions cannot be established. Free-tier terms may
  change (so far they have only improved).
- **Metadata exposure:** Tailscale Inc. sees which devices exist and when they connect
  — not traffic contents.
- **Escape hatches keep the lock-in shallow:**
  - [headscale](https://github.com/juanfont/headscale) is an open source control plane
    compatible with official Tailscale clients, if self-hosting the coordination layer
    ever becomes necessary (at the cost of a persistent server, against the
    pay-per-use philosophy).
  - The data plane is plain WireGuard and the ephemeral-EC2 pattern is unchanged in the
    DIY alternative (§3.5): migrating later discards almost nothing. The orchestrator
    should keep the Tailscale-specific surface small in case a WireGuard-native backend
    is added later.
- **Not suitable for streaming geo-unblocking** (non-goal, restated because it is the
  most common false expectation for this kind of tool).
- **Tagged-device limits:** infrastructure ("tagged") devices are subject to per-plan
  caps on the free tier; with one ephemeral exit node at a time this is irrelevant, but
  it is the parameter to check if the design ever changes to many persistent nodes.

## 6. Cost model (reference)

Assumptions: ~10 hours/month of use, `t4g.nano` on-demand, one 2-hour session at a
time, moderate browsing traffic.

| Item | Rate | Per 2h session | Per month (~10h) |
| --- | --- | --- | --- |
| EC2 `t4g.nano` (on-demand) | ~$0.0042/h | ~$0.01 | ~$0.04 |
| Public IPv4 | $0.005/h | ~$0.01 | ~$0.05 |
| Egress traffic | ~$0.09/GB | $0.10–0.30 (1–3 GB) | $0.90–2.70 |
| Tailscale Personal plan | free | — | — |
| **Total** | | **~$0.15–0.30** | **~$1–3** |

Standing cost while idle: **$0**, provided teardown is complete — no reserved Elastic
IPs (use the dynamic public IP; the exit node is re-discovered through the tailnet each
session anyway), EBS volume deleted with the instance, no leftover snapshots.

## 7. References

- Tailscale: [pricing](https://tailscale.com/pricing) ·
  [pricing v4 announcement](https://tailscale.com/blog/pricing-v4) ·
  [exit nodes](https://tailscale.com/docs/features/exit-nodes) ·
  [ephemeral nodes](https://tailscale.com/docs/features/ephemeral-nodes) ·
  [auth keys](https://tailscale.com/docs/features/access-control/auth-keys) ·
  [auto approvers](https://tailscale.com/blog/auto-approvers)
- [headscale](https://github.com/juanfont/headscale) (self-hosted control plane)
- [Algo VPN](https://github.com/trailofbits/algo)
- [Outline VPN](https://outline-vpn.com/)
- [AWS VPN pricing](https://aws.amazon.com/vpn/pricing/)
- [Lightsail data transfer allowance FAQ](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-data-transfer-allowance.html)
  (proration behavior)
- Prior art for the Tailscale-on-AWS pattern:
  [Automating Tailscale exit nodes on AWS](https://blog.scottgerring.com/posts/automating-tailscale-exit-nodes-on-aws/)
- Prior art for the DIY alternative:
  [jmhale/terraform-aws-wireguard](https://github.com/jmhale/terraform-aws-wireguard) ·
  [Temporary cloud VPN with EC2 and WireGuard](https://www.edrandall.uk/posts/wireguard-ec2-vpn/)
