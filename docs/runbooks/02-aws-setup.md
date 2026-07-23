# Runbook 02 — AWS setup (one-time)

Creates a minimally-privileged AWS identity for the CLI and configures local
credentials. You do this once per AWS account.

**Time required:** ~15 minutes.

## Prerequisites

- A personal AWS account, and admin access to it (root or an admin IAM
  identity) to perform the steps below.
- The [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  installed locally, or willingness to do the same steps in the console.

## Steps

### 1. Create the IAM policy

The policy in [`docs/iam-policy.json`](../iam-policy.json) is the minimal set
of permissions the CLI needs. It is scoped so that:

- instances can only be **launched** if tagged `Project=vpn-aws` at creation;
- instances can only be **terminated** if they carry that tag;
- everything else is read-only (`Describe*`, AMI lookup via SSM public
  parameters, console output for debugging).

Using an admin profile:

```sh
aws iam create-policy \
  --policy-name vpn-aws \
  --policy-document file://docs/iam-policy.json
```

Note the policy ARN in the output (`arn:aws:iam::<ACCOUNT_ID>:policy/vpn-aws`).

### 2. Create the IAM user and access key

```sh
aws iam create-user --user-name vpn-aws
aws iam attach-user-policy \
  --user-name vpn-aws \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/vpn-aws
aws iam create-access-key --user-name vpn-aws
```

Save the `AccessKeyId` and `SecretAccessKey` from the last command.

> If your account uses IAM Identity Center (SSO), create a permission set
> from the same policy document instead, and configure
> `aws configure sso`; then set that profile name in the CLI config.

### 3. Configure the local profile

```sh
aws configure --profile vpn-aws
# AWS Access Key ID:      <AccessKeyId>
# AWS Secret Access Key:  <SecretAccessKey>
# Default region name:    (any, e.g. eu-central-1 — the CLI is region-explicit)
# Default output format:  json
```

You will reference this profile as `profile = "vpn-aws"` in the CLI config
([runbook 03](03-install-and-configure.md)).

### 4. (Recommended) Create a billing guard

A pay-per-use setup deserves a tripwire. In the console:
**Billing → Budgets → Create budget** → monthly cost budget of e.g. **$10**
with an email alert. If anything is ever left running by mistake, you hear
about it for cents, not for a full bill.

## Verification

```sh
aws sts get-caller-identity --profile vpn-aws   # shows the vpn-aws user ARN
aws ec2 describe-regions --profile vpn-aws --query 'Regions[].RegionName'
```

Both commands must succeed. Full end-to-end verification happens in
[runbook 04](04-start-session.md).

## Notes

- **Default VPC requirement:** the CLI launches into the region's default
  VPC. All regions have one unless it was deliberately deleted; if so, see
  [runbook 06](06-troubleshooting.md).
- The access key only grants the scoped policy above; still, treat it as a
  secret. Rotate it from the IAM console if it ever leaks.
