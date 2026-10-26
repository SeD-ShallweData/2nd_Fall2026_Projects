# GCP runtime source and verification scope

As of 2026-10-26, the judging dashboard runs on a separate Ubuntu 24.04 GCP VM with systemd, a Tailscale Funnel HTTPS endpoint and secret-link authentication. The complete access link is a credential and is supplied separately through an authorized channel. The earlier STEP4A Cloudflare/local-PC instructions describe the previous demo.

## Included source

`infra/gcp/` contains the backend adapter, persistent gateway, SQLite state/budget handling, verified-cache adapter, health checks, frontend overlay, unit files and synthetic tests used by the deployed service. Runtime source is copied byte-for-byte from the reviewed deployment inventory, except that the install script's personal staging directory is replaced by the required `BBIGEUDEOK_STAGING_DIR` environment variable. Account-specific provisioning, authentication helpers, keys, cookies, logs, databases, data archives and the private migration inventory are excluded.

This is source for inspection, not a one-command deployment. Do not run bootstrap or installation scripts on an existing machine without reviewing disk paths, mounts, data admission and credentials. `settings.example.json` does not grant data-transfer permission. An actual migration inventory must be generated and reviewed by an authorized data holder; it verifies protected file hashes before the backend starts. Copying source does not start or change the existing service.

## Tests without external API calls

From the team directory:

```text
python -B -m unittest discover -s infra/gcp -p "test_*.py" -v
node --test tests/test_ui_state.cjs
```

Tests use temporary synthetic SQLite files and loopback HTTP servers. They do not call Upstage or Seoul APIs or load competition data. The Linux listener-reuse test is skipped on Windows.

## Current operational behavior

The default Agent mode is structured. Actual Solar Tool Calling is selectable when the provider is ready; allowed tool selections are checked and the final facts are rendered from verified server data. Persistent limits are 30 LLM questions per authenticated session per rolling hour, 100 per session per rolling day, 1,000 globally per rolling day, and 4,000 provider reservations per rolling day / 120 per minute. All Agent questions share a 30-per-minute limit and one concurrent Agent slot. There is no cumulative lifetime cutoff. Application limits do not guarantee provider credit or account quota.

Synthetic tests, actual HTTPS checks, representative real LLM responses, restart recovery and VM reboot persistence have been checked separately. Separate-device/network testing, a production multi-user load test and 24-hour uninterrupted operation remain unverified. These limits do not change the submitted classification rules or data.

Data hosting and third-party transmission rights remain unconfirmed. User authorization to deploy is separate from organizer or data-owner permission. The Seoul cache is read locally; normal browsing does not refresh the Seoul API. A previous explicitly authorized HTTP refresh does not establish a secure HTTPS refresh path.
