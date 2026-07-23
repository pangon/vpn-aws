#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.34",
# ]
# ///
"""vpn-aws — ephemeral personal VPN exit nodes on AWS, powered by Tailscale.

Each session creates a single tagged EC2 instance that joins your tailnet
as an ephemeral exit node; teardown terminates it and nothing is left
behind. See README.md and docs/runbooks/ for setup and usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

PROJECT_TAG_KEY = "Project"
PROJECT_TAG_VALUE = "vpn-aws"
TERMINATE_AFTER_TAG = "vpn-aws:terminate-after"
HOSTNAME_PREFIX = "vpn-aws"
TS_API = "https://api.tailscale.com/api/v2"
AMI_SSM_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
AUTH_KEY_EXPIRY_SECONDS = 900  # the key is only needed while the instance boots
DEFAULT_TTL = "4h"
MIN_TTL_MINUTES = 5
MAX_TTL_MINUTES = 24 * 60
UP_TIMEOUT_SECONDS = 360
LIVE_STATES = ["pending", "running", "stopping", "stopped"]

# The watchdog is armed before anything else so the instance self-destructs
# at TTL expiry even if the rest of the boot fails (the instance is launched
# with InstanceInitiatedShutdownBehavior=terminate).
USER_DATA_TEMPLATE = """\
#!/bin/bash
set -ux

shutdown -P +{ttl_minutes}

cat > /etc/sysctl.d/99-tailscale.conf <<'SYSCTL'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
SYSCTL
sysctl -p /etc/sysctl.d/99-tailscale.conf

# UDP throughput tuning recommended by Tailscale for exit nodes; the node
# works without it.
IFACE=$(ip -o route get 8.8.8.8 | awk '{{print $5}}')
ethtool -K "$IFACE" rx-udp-gro-forwarding on rx-gro-list off || true

dnf config-manager --add-repo https://pkgs.tailscale.com/stable/amazon-linux/2023/tailscale.repo
dnf install -y tailscale
systemctl enable --now tailscaled
tailscale up --auth-key='{auth_key}' --hostname='{hostname}' --advertise-exit-node --ssh
"""


class ApiError(RuntimeError):
    """A failure with a message meant for the user."""


@dataclass
class Config:
    oauth_client_id: str
    oauth_client_secret: str
    tailnet: str = "-"
    tag: str = "tag:vpn-aws"
    profile: str = ""
    instance_type: str = "t4g.nano"


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "vpn-aws" / "config.toml"


def load_config() -> Config:
    path = config_path()
    if not path.is_file():
        raise ApiError(
            f"config file not found: {path}\n"
            "Create it from config.example.toml "
            "(see docs/runbooks/03-install-and-configure.md)."
        )
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    ts = raw.get("tailscale", {})
    aws = raw.get("aws", {})
    cfg = Config(
        oauth_client_id=ts.get("oauth_client_id", ""),
        oauth_client_secret=ts.get("oauth_client_secret", ""),
        tailnet=ts.get("tailnet", "-") or "-",
        tag=ts.get("tag", "tag:vpn-aws"),
        profile=aws.get("profile", ""),
        instance_type=aws.get("instance_type", "t4g.nano"),
    )
    if (
        not cfg.oauth_client_id
        or not cfg.oauth_client_secret
        or "REPLACE" in cfg.oauth_client_id + cfg.oauth_client_secret
    ):
        raise ApiError(
            f"tailscale.oauth_client_id / oauth_client_secret not set in {path}\n"
            "(see docs/runbooks/01-tailscale-setup.md)."
        )
    if not cfg.tag.startswith("tag:"):
        raise ApiError(f'tailscale.tag must start with "tag:" (got "{cfg.tag}")')
    return cfg


def parse_ttl(text: str) -> int:
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", text.strip())
    if not match or (match.group(1) is None and match.group(2) is None):
        raise ApiError(f'invalid --ttl "{text}": use forms like 90m, 2h, 1h30m')
    minutes = int(match.group(1) or 0) * 60 + int(match.group(2) or 0)
    if not MIN_TTL_MINUTES <= minutes <= MAX_TTL_MINUTES:
        raise ApiError(f"--ttl must be between {MIN_TTL_MINUTES}m and 24h")
    return minutes


# ---------------------------------------------------------------------------
# Tailscale API


def ts_request(method: str, path: str, *, token: str = "", data=None, form: bool = False):
    headers = {"Accept": "application/json"}
    body = None
    if form:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(TS_API + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()[:300]
        raise ApiError(f"Tailscale API {method} {path}: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Tailscale API unreachable: {exc.reason}") from exc
    return json.loads(payload) if payload else {}


def ts_token(cfg: Config) -> str:
    resp = ts_request(
        "POST",
        "/oauth/token",
        data={"client_id": cfg.oauth_client_id, "client_secret": cfg.oauth_client_secret},
        form=True,
    )
    return resp["access_token"]


def ts_tailnet_path(cfg: Config) -> str:
    return f"/tailnet/{urllib.parse.quote(cfg.tailnet, safe='')}"


def ts_mint_auth_key(cfg: Config, token: str, description: str) -> str:
    body = {
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": False,
                    "ephemeral": True,
                    "preauthorized": True,
                    "tags": [cfg.tag],
                }
            }
        },
        "expirySeconds": AUTH_KEY_EXPIRY_SECONDS,
        "description": description,
    }
    resp = ts_request("POST", f"{ts_tailnet_path(cfg)}/keys", token=token, data=body)
    return resp["key"]


def ts_devices(cfg: Config, token: str) -> list[dict]:
    resp = ts_request("GET", f"{ts_tailnet_path(cfg)}/devices", token=token)
    return resp.get("devices", [])


# ---------------------------------------------------------------------------
# AWS


def aws_session(cfg: Config) -> boto3.session.Session:
    return boto3.session.Session(profile_name=cfg.profile or None)


def enabled_regions(session: boto3.session.Session) -> list[str]:
    ec2 = session.client("ec2", region_name="us-east-1")
    return sorted(r["RegionName"] for r in ec2.describe_regions()["Regions"])


def resolve_ami(session: boto3.session.Session, region: str) -> str:
    ssm = session.client("ssm", region_name=region)
    return ssm.get_parameter(Name=AMI_SSM_PARAM)["Parameter"]["Value"]


def find_instances(session: boto3.session.Session, region: str, states=None) -> list[dict]:
    ec2 = session.client("ec2", region_name=region)
    resp = ec2.describe_instances(
        Filters=[
            {"Name": f"tag:{PROJECT_TAG_KEY}", "Values": [PROJECT_TAG_VALUE]},
            {"Name": "instance-state-name", "Values": states or LIVE_STATES},
        ]
    )
    found = []
    for reservation in resp["Reservations"]:
        for inst in reservation["Instances"]:
            tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
            found.append(
                {
                    "region": region,
                    "id": inst["InstanceId"],
                    "state": inst["State"]["Name"],
                    "name": tags.get("Name", "?"),
                    "public_ip": inst.get("PublicIpAddress", "-"),
                    "launched": inst.get("LaunchTime"),
                    "terminate_after": tags.get(TERMINATE_AFTER_TAG, "?"),
                }
            )
    return found


def scan_regions(session: boto3.session.Session, regions: list[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=min(16, len(regions) or 1)) as pool:
        batches = pool.map(lambda r: find_instances(session, r), regions)
    return [inst for batch in batches for inst in batch]


# ---------------------------------------------------------------------------
# Commands


def cmd_up(args) -> int:
    cfg = load_config()
    session = aws_session(cfg)
    region = args.region
    ttl_minutes = parse_ttl(args.ttl)
    hostname = f"{HOSTNAME_PREFIX}-{region}"

    if region not in enabled_regions(session):
        raise ApiError(f"{region} is not an enabled region on this account (list with: ./vpn.py regions)")

    existing = find_instances(session, region)
    if existing:
        ids = ", ".join(i["id"] for i in existing)
        raise ApiError(f"an exit node already exists in {region} ({ids}); run ./vpn.py down {region} first")

    print(f"Minting ephemeral Tailscale auth key (tag {cfg.tag})...")
    token = ts_token(cfg)
    auth_key = ts_mint_auth_key(cfg, token, hostname)

    ami = resolve_ami(session, region)
    terminate_after = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    user_data = USER_DATA_TEMPLATE.format(
        ttl_minutes=ttl_minutes, auth_key=auth_key, hostname=hostname
    )

    ec2 = session.client("ec2", region_name=region)
    print(f"Launching {cfg.instance_type} in {region} (AMI {ami}, TTL {ttl_minutes}m)...")
    try:
        resp = ec2.run_instances(
            ImageId=ami,
            InstanceType=cfg.instance_type,
            MinCount=1,
            MaxCount=1,
            InstanceInitiatedShutdownBehavior="terminate",
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": PROJECT_TAG_KEY, "Value": PROJECT_TAG_VALUE},
                        {"Key": "Name", "Value": hostname},
                        {"Key": TERMINATE_AFTER_TAG, "Value": terminate_after},
                    ],
                }
            ],
            UserData=user_data,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "VPCIdNotSpecified":
            raise ApiError(
                f"no default VPC in {region}; recreate it once with an admin identity:\n"
                f"  aws ec2 create-default-vpc --region {region}\n"
                "(see docs/runbooks/06-troubleshooting.md)"
            ) from exc
        raise

    instance_id = resp["Instances"][0]["InstanceId"]
    print(f"Instance {instance_id} launched; waiting for it to join the tailnet...")

    deadline = time.monotonic() + UP_TIMEOUT_SECONDS
    seen_running = False
    while time.monotonic() < deadline:
        time.sleep(5)
        if not seen_running:
            inst = find_instances(session, region, states=["pending", "running"])
            if inst and inst[0]["state"] == "running":
                seen_running = True
                print(f"Instance is running (public IP {inst[0]['public_ip']}); installing Tailscale...")
            continue
        devices = ts_devices(cfg, token)
        if any(d.get("hostname") == hostname for d in devices):
            print()
            print(f"Exit node '{hostname}' is online in your tailnet.")
            print("  - Select it as exit node in any Tailscale client (see docs/runbooks/04-start-session.md).")
            print(f"  - It self-destructs at ~{terminate_after} (TTL {args.ttl}).")
            print(f"  - End the session earlier with: ./vpn.py down {region}")
            return 0

    print(
        f"Timed out after {UP_TIMEOUT_SECONDS}s waiting for '{hostname}' to appear in the tailnet.\n"
        f"The instance is still running: inspect with ./vpn.py status, clean up with\n"
        f"./vpn.py down {region}. See docs/runbooks/06-troubleshooting.md.",
        file=sys.stderr,
    )
    return 1


def cmd_down(args) -> int:
    cfg = load_config()
    session = aws_session(cfg)
    regions = [args.region] if args.region else enabled_regions(session)
    print(f"Scanning {len(regions)} region(s) for tagged instances...")
    instances = scan_regions(session, regions)
    if not instances:
        print("Nothing to terminate: no vpn-aws instances found.")
        return 0
    print("Reminder: deselect the exit node in your Tailscale clients, or their")
    print("traffic will stall until you do.")
    for inst in instances:
        ec2 = session.client("ec2", region_name=inst["region"])
        ec2.terminate_instances(InstanceIds=[inst["id"]])
        print(f"  {inst['region']}: terminating {inst['id']} ({inst['name']}, was {inst['state']})")
    print("Done. The ephemeral node(s) will disappear from the tailnet on their own.")
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    session = aws_session(cfg)
    regions = enabled_regions(session)
    print(f"Scanning {len(regions)} regions for tagged instances...")
    instances = scan_regions(session, regions)
    if instances:
        print(f"\nAWS instances (tag {PROJECT_TAG_KEY}={PROJECT_TAG_VALUE}):")
        for i in instances:
            launched = i["launched"].astimezone().strftime("%Y-%m-%d %H:%M") if i["launched"] else "?"
            print(
                f"  {i['region']:<16} {i['id']:<21} {i['state']:<10} "
                f"ip={i['public_ip']:<16} launched={launched}  self-destructs={i['terminate_after']}"
            )
    else:
        print("\nNo vpn-aws instances in any region.")

    exit_code = 0
    try:
        token = ts_token(cfg)
        devices = [d for d in ts_devices(cfg, token) if cfg.tag in d.get("tags", [])]
    except ApiError as exc:
        print(f"\nWarning: could not query the Tailscale API: {exc}", file=sys.stderr)
        devices, exit_code = [], 1
    else:
        if devices:
            print(f"\nTailnet devices tagged {cfg.tag}:")
            for d in devices:
                print(f"  {d.get('hostname', '?'):<28} lastSeen={d.get('lastSeen', '?')}")
        else:
            print(f"\nNo tailnet devices tagged {cfg.tag}.")
    return exit_code


def cmd_regions(args) -> int:
    cfg = load_config()
    for name in enabled_regions(aws_session(cfg)):
        print(name)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vpn.py",
        description="Ephemeral personal VPN exit nodes on AWS, powered by Tailscale.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("up", help="create an ephemeral exit node in an AWS region")
    p_up.add_argument("region", help="AWS region, e.g. eu-central-1 (list with: vpn.py regions)")
    p_up.add_argument(
        "--ttl",
        default=DEFAULT_TTL,
        help=f"self-destruct timeout, e.g. 90m, 2h, 1h30m (default {DEFAULT_TTL})",
    )
    p_up.set_defaults(func=cmd_up)

    p_down = sub.add_parser("down", help="terminate exit nodes (all regions, or one)")
    p_down.add_argument("region", nargs="?", help="only this region (default: every region)")
    p_down.set_defaults(func=cmd_down)

    p_status = sub.add_parser("status", help="show instances and tailnet exit nodes")
    p_status.set_defaults(func=cmd_status)

    p_regions = sub.add_parser("regions", help="list enabled AWS regions")
    p_regions.set_defaults(func=cmd_regions)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except NoCredentialsError:
        print("error: no AWS credentials found (see docs/runbooks/02-aws-setup.md)", file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as exc:
        print(f"error: AWS API: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
