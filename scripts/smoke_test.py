#!/usr/bin/env python3
"""Automated Post-Deployment Staging Smoke Test Suite.

Verifies:
1. Health endpoint liveness and database connectivity (/api/health/).
2. JWT token obtain (/api/auth/token/) and token refresh (/api/auth/token/refresh/).
3. Dynamic organization & tenant database provisioning (/api/organizations/).
4. Multi-tenant project creation and subscription quota enforcement (/api/projects/).
5. Cross-tenant isolation and unauthorized access rejection.

Usage:
    python scripts/smoke_test.py --base-url https://staging.example.com \
        --username staging_admin --password <password>
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid


def http_request(url, method="GET", data=None, headers=None):
    headers = headers or {}
    encoded_data = None
    if data is not None:
        encoded_data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return err.code, parsed


def run_smoke_tests(base_url, username, password):
    base = base_url.rstrip("/")
    print(f"=== Starting Post-Deployment Smoke Tests against {base} ===")

    # 1. Health Probe
    print("\n[1/6] Probing /api/health/ ...")
    status, body = http_request(f"{base}/api/health/")
    if status != 200 or body.get("status") != "ok":
        print(f"FAILED: Expected 200 with status=ok, got {status}: {body}")
        return False
    print("  PASS: Health check reports status ok.")

    # 2. Authentication (Token Obtain)
    print("\n[2/6] Authenticating via /api/auth/token/ ...")
    status, body = http_request(
        f"{base}/api/auth/token/",
        method="POST",
        data={"username": username, "password": password},
    )
    if status != 200 or "access" not in body:
        print(f"FAILED: Expected 200 with access token, got {status}: {body}")
        return False
    access_token = body["access"]
    refresh_token = body.get("refresh")
    auth_header = {"Authorization": f"Bearer {access_token}"}
    print("  PASS: Authentication successful, received JWT access token.")

    # 3. Token Refresh
    print("\n[3/6] Rotating token via /api/auth/token/refresh/ ...")
    status, body = http_request(
        f"{base}/api/auth/token/refresh/",
        method="POST",
        data={"refresh": refresh_token},
    )
    if status != 200 or "access" not in body:
        print(f"FAILED: Expected 200 with new access token, got {status}: {body}")
        return False
    # Use rotated access token
    access_token = body["access"]
    auth_header = {"Authorization": f"Bearer {access_token}"}
    print("  PASS: Token refresh successful.")

    # 4. Organization Provisioning
    unique_suffix = uuid.uuid4().hex[:6]
    org_name = f"Smoke Org {unique_suffix}"
    print(f"\n[4/6] Creating organization '{org_name}' via /api/organizations/ ...")
    status, body = http_request(
        f"{base}/api/organizations/",
        method="POST",
        data={"name": org_name},
        headers=auth_header,
    )
    if status != 201 or "slug" not in body:
        print(f"FAILED: Expected 201 Created with slug, got {status}: {body}")
        return False
    org_slug = body["slug"]
    print(f"  PASS: Tenant provisioned successfully (slug: {org_slug}).")

    tenant_headers = {
        **auth_header,
        "X-Organization": org_slug,
    }

    # 5. Project Creation and Quota Enforcement
    print("\n[5/6] Verifying project creation and plan quota enforcement ...")
    for i in range(1, 4):
        status, body = http_request(
            f"{base}/api/projects/",
            method="POST",
            data={"name": f"Project {i}", "description": f"Smoke test project {i}"},
            headers=tenant_headers,
        )
        if status != 201:
            print(f"FAILED: Expected 201 for project {i}, got {status}: {body}")
            return False
        print(f"  Project {i}/3 created successfully.")

    # Attempt 4th project (Free plan limit is 3 projects)
    status, body = http_request(
        f"{base}/api/projects/",
        method="POST",
        data={"name": "Project 4", "description": "Should exceed limit"},
        headers=tenant_headers,
    )
    if status != 403:
        print(f"FAILED: Expected 403 Forbidden on quota limit, got {status}: {body}")
        return False
    print("  PASS: Quota correctly enforced (4th project blocked with 403 Forbidden).")

    # 6. Tenant Isolation Check
    print("\n[6/6] Verifying cross-tenant isolation ...")
    # Request projects without X-Organization header
    status, body = http_request(f"{base}/api/projects/", headers=auth_header)
    if status != 403:
        print(f"FAILED: Expected 403 Forbidden without organization header, got {status}: {body}")
        return False
    print("  PASS: Cross-tenant isolation confirmed (unscoped requests rejected).")

    print("\n=== ALL 6 POST-DEPLOYMENT SMOKE TESTS PASSED SUCCESSFULLY ===")
    return True


def main():
    parser = argparse.ArgumentParser(description="Staging Post-Deployment Smoke Tests")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL of API")
    parser.add_argument("--username", required=True, help="Username of pre-created user")
    parser.add_argument("--password", required=True, help="Password of pre-created user")
    args = parser.parse_args()

    success = run_smoke_tests(args.base_url, args.username, args.password)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
