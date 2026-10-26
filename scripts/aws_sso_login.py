"""AWS IAM Identity Center (SSO) browser sign-in without the AWS CLI (no admin rights needed).

Usage (from backend/, Windows shown; use `export` on macOS/Linux):
    set AWS_SSO_START_URL=https://<your-org>.awsapps.com/start   (required: your organisation's portal)
    set AWS_SSO_REGION=us-east-1                                  (optional: Identity Center region)
    set AWS_SSO_SESSION=office-vision-sso                         (optional: name of the config section)
    .venv/Scripts/python.exe ../scripts/aws_sso_login.py
Opens your browser with a one-time code; approve it. Writes ~/.aws/config [sso-session <AWS_SSO_SESSION>]
and a botocore-compatible token cache, so boto3 (profiles "default" / "office-vision")
works and refreshes credentials automatically. Never prints tokens.
"""
import datetime as dt, hashlib, json, os, sys, time, webbrowser
from pathlib import Path
import boto3

START_URL = os.environ.get("AWS_SSO_START_URL", "").strip()
SSO_REGION = os.environ.get("AWS_SSO_REGION", "us-east-1")
SESSION = os.environ.get("AWS_SSO_SESSION", "office-vision-sso")
if not START_URL.startswith("https://"):
    sys.exit("Set AWS_SSO_START_URL to your organisation's IAM Identity Center portal, e.g. https://<org>.awsapps.com/start")

oidc = boto3.client("sso-oidc", region_name=SSO_REGION)
reg = oidc.register_client(clientName="office-vision-poc", clientType="public",
                           scopes=["sso:account:access"],
                           grantTypes=["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"])
auth = oidc.start_device_authorization(clientId=reg["clientId"], clientSecret=reg["clientSecret"], startUrl=START_URL)
print("USER CODE (must match the browser page):", auth["userCode"], flush=True)
webbrowser.open(auth["verificationUriComplete"])
print("Browser opened (or open this link yourself):", auth["verificationUriComplete"], flush=True)
print("Waiting for approval...", flush=True)

deadline = time.time() + min(auth["expiresIn"], 600)
interval = auth.get("interval", 5)
token = None
while time.time() < deadline:
    time.sleep(interval)
    try:
        token = oidc.create_token(clientId=reg["clientId"], clientSecret=reg["clientSecret"],
                                  grantType="urn:ietf:params:oauth:grant-type:device_code",
                                  deviceCode=auth["deviceCode"])
        break
    except oidc.exceptions.AuthorizationPendingException:
        continue
    except oidc.exceptions.SlowDownException:
        interval += 5
if token is None:
    sys.exit("Timed out or denied: no approval received in the browser.")

now = dt.datetime.now(dt.timezone.utc)
fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
cache = {
    "startUrl": START_URL, "region": SSO_REGION,
    "accessToken": token["accessToken"],
    "expiresAt": fmt(now + dt.timedelta(seconds=token["expiresIn"])),
    "clientId": reg["clientId"], "clientSecret": reg["clientSecret"],
    "registrationExpiresAt": fmt(dt.datetime.fromtimestamp(reg["clientSecretExpiresAt"], dt.timezone.utc)),
}
if token.get("refreshToken"):
    cache["refreshToken"] = token["refreshToken"]
cache_dir = Path.home() / ".aws" / "sso" / "cache"
cache_dir.mkdir(parents=True, exist_ok=True)
(cache_dir / (hashlib.sha1(SESSION.encode()).hexdigest() + ".json")).write_text(json.dumps(cache), encoding="utf-8")

cfg = Path.home() / ".aws" / "config"
text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
if f"[sso-session {SESSION}]" not in text:
    text += f"\n[sso-session {SESSION}]\nsso_start_url = {START_URL}\nsso_region = {SSO_REGION}\nsso_registration_scopes = sso:account:access\n"
    cfg.write_text(text, encoding="utf-8")

print("SIGNED IN. token valid until", cache["expiresAt"], "| refresh token:", "yes" if "refreshToken" in cache else "no")
sso = boto3.client("sso", region_name=SSO_REGION)
for acct in sso.get_paginator("list_accounts").paginate(accessToken=token["accessToken"]).build_full_result()["accountList"]:
    roles = sso.get_paginator("list_account_roles").paginate(accessToken=token["accessToken"], accountId=acct["accountId"]).build_full_result()["roleList"]
    print(f"ACCOUNT {acct['accountId']}  name={acct.get('accountName')!r}  roles={[r['roleName'] for r in roles]}")
