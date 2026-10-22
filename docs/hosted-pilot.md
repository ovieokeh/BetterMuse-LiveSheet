# Restricted hosted pilot

Endpoint: `https://livesheet.bettermuse.app`. Host: `k3o-runner`.
Managed in the existing BetterMuse Coolify project as **LiveSheet restricted pilot**.
This is an invited pilot, not a public upload endpoint or a Pro entitlement system.

## Access

Every job and file needs a bearer token. Tokens expire after 30 days by default.
Only hashes are stored in `/opt/livesheet-pilot/secrets/access.json` (0600).
The registry directory is mounted read-only and reloaded on every request.
Removing a record revokes it immediately. A malformed registry denies access.
Use the same principal ID when rotating credentials so its jobs remain accessible.

Issue tokens with `tools/pilot_credentials.py`. Deliver the token privately.
Do not embed a shared credential in an app binary, browser JavaScript, or Git.
The local private copies live under ignored `data/pilot-*-token` files.
Operators can read `/metrics`; testers cannot. Operators do not get access to
other testers' PDFs. `/health` exposes only a boolean and requires no token.
Authenticated clients can retrieve the corresponding source at `/source`.

Default limits per tester: two unfinished jobs and 20 admitted attempts per
rolling 24 hours. Retries and invalid-PDF attempts count. Idempotent replays do
not. Deleting jobs does not reset the quota. The SQLite ledger survives restarts.
The existing global queue limit, 25 MiB/20-page cap, seven-day retention,
40-minute job timeout, and 10 GiB free-space floor remain in force.

## Runtime

`deploy/compose.pilot.yaml` is the reviewed input to Coolify. Set the service domain
to `https://livesheet.bettermuse.app:3176` in Coolify. Port 3176 is internal;
only the reverse proxy exposes HTTP/HTTPS. HTTP redirects to HTTPS. Noindex is set.
Two workers share a 3 GiB container limit and two CPUs. Runtime is non-root,
read-only, without Linux capabilities. Job storage is persistent and private.

This first pilot transfers the verified runtime image directly over SSH.
There is no public registry or repository push. `deploy/Dockerfile.pilot` installs
the reviewed Python package over that base. Verify its base image identity before
building; record the final image identity before deployment. Keep the previous
image for rollback. The canonical Dockerfile remains the clean full-build path.

`tools/pilot_source.py` creates the source archive. It excludes uploads, secrets,
private test data, and binary score assets. Include it in the pilot image.
No private local uploads are migrated. Remote acceptance tests use generated PDFs.

## Monitoring

Coolify Sentinel supplies host CPU, memory, disk, and container status.
Docker probes `/health` every 30 seconds. It fails when workers stop, their
watchdog stops responding for 90 seconds, required tools disappear, or free disk
falls below 10 GiB. A long conversion alone is not an unhealthy service.

The operator `/metrics` endpoint reports queue depth, oldest queued age, worker
counts, free disk, and recent failures. Application logs contain job IDs and
state transitions. HTTP logs omit tokens, IP addresses, filenames, and queries.
Container logs rotate at three 10 MB files. Recognition failures remain job
failures; they do not trigger a service restart or bypass output validation.

Run `python -m livesheet.monitor` as a Coolify scheduled task every five minutes.
It fails on unhealthy workers, less than 15 GiB free, queued work older than ten
minutes, jobs running over 45 minutes, or three failures within 15 minutes.
Failures notify through Coolify’s existing scheduled-task failure channel.

Review queue age above 10 minutes, repeated processing failures, sustained memory
above 85%, and disk approaching 10 GiB free. Coolify notifications must be routed
to an existing verified destination before relying on unattended alerts.
Docker health checks detect faults; Docker itself does not restart an unhealthy
but still-running process. A process exit restarts automatically.

## Recovery and updates

1. Stop accepting test traffic before maintenance.
2. Stop the service through Coolify. The 30-second grace period lets both workers
   terminate their tool processes. Their jobs remain recoverable in storage.
3. Back up the persistent directory and private credential registry while stopped.
   Keep backups private. Do not retain uploaded PDFs indefinitely in backups.
4. Load/build the next version and verify the image identity. Change only the
   image tag in Coolify, then deploy. Never overwrite an existing release tag.
5. Check health, authenticated access, and one generated conversion after restart.
6. To roll back, restore the previous image tag. Do not delete the job volume.

Current pilot storage is single-host. Hardware loss can lose queued uploads and
results; clients must retain the original PDF and download completed companions.
Off-host backup policy and account/Pro entitlement integration are release work
before opening this beyond invited testers.

## Deployment evidence — 2026-10-22

- Coolify service: `ilhn8tr5jnemvjh1bndk9ctx`, server `imie456dkbaxltinfarfuj19`.
- Image: `bettermuse-livesheet:pilot-20261022-r2`.
- Image identity: `sha256:9f539a533fb306e681870290d0c9afb4c1945c548340e23d9fc39f3da8973e2b`.
- Full Linux suite: 263 passing tests. The two additional monitor tests and four
  pilot tests also passed on the hosted Linux machine.
- Two concurrent generated-score conversions passed through the public HTTPS
  endpoint. After forcibly killing the service process, Docker restarted it
  automatically and both interrupted jobs recovered. Both outputs matched their
  saved musical references. A separate active cancellation and retry also passed
  and matched its reference. No private PDFs were uploaded.
- The first crash-test command used `docker kill`, which Docker treats as an
  intentional stop. That trial required a manual start and is not evidence of
  automatic recovery. The subsequent process-level SIGKILL test produced
  `RestartCount=1` and is the automatic-recovery check reported above.
- A restart initiated through Coolify completed and retained the three results.
- Missing credentials are rejected. Cross-principal job/file access is rejected.
  The temporary verification credential was revoked without restarting the
  service. Raw service ports 3176/3177 are not publicly reachable.
- Scheduled task `jezmfamgyfes6a75jf1ldezp` runs the monitor every five minutes.
  Its first automatic run succeeded at 11:15 UTC, with no queue or worker faults.
- Evidence is in ignored `artifacts/pilot-*`. The failed first test harness log
  remains for transparency; `pilot-https-recovery.log` records the recovered jobs.

Client configuration has not been changed to the hosted pilot yet. The website
and app still need a deliberate test-client cutover using private credentials.
Do not ship the pilot credential in the production app or website JavaScript.

### Existing infrastructure issues found during setup

Coolify's sender is `alert@dashboard.ovie.dev`. Resend rejected it because its
verification records were absent after the domain moved to Cloudflare DNS.
The existing DKIM TXT record and the `rsend.dashboard` / `send.dashboard` CNAME
records were restored from Resend's displayed values, with DNS-only mode.
Resend verified the restored domain. A fresh Coolify test email was sent and the
user confirmed receipt. No sender, recipient, or SMTP credential was changed.

Cloudflare Access initially redirected Sentinel's push requests to an interactive
login. With explicit approval, a separate Access application now matches only
`admin.ovie.dev/api/v1/sentinel/push`, with a Bypass policy restricted to
`2.29.120.87/32`. Coolify still checks the Sentinel token. Unauthenticated POSTs
from the runner return 401; another source address returns 403. Dashboard access
from the runner still redirects to Cloudflare login (302).

Access application: `4baa6386-0cc9-41fc-b120-252c7443aab3`.
Policy: `65ac7fa7-82f1-496c-b127-492c39a316b2`.
If the runner address changes, update this exact-IP policy deliberately. Do not
broaden it to the dashboard or all API paths. The scheduled service check works
independently through Coolify's SSH connection.

Sentinel subsequently reported **In sync** and the server header became **Ready**.
Email delivery was confirmed by the user. The restricted hosted pilot is active;
client cutover and off-host storage recovery remain separate follow-up work.


## Test-client cutover — 22 October 2026

- Local website uses the `ovie-web` tester credential from the private
  `data/pilot-web-token` file. Next.js injects it server-side. Browser code receives
  neither a token nor the token-file path. Loopback and same-origin guards remain.
- BetterMuse Dev uses the `ovie` tester credential from `data/pilot-ovie-token`.
  The launch helper writes it to the device-only Keychain through a Debug-only
  launch environment. It is not compiled into the app. Release remains disabled.
- Both credentials expire on 21 November 2026. Each retains the existing limits.
  These are invited test credentials, not public account or Pro entitlements.
- Browser-generated upload: `33ef53034db5494899e25ccd988b2768`.
- Native URLSession upload/resume: `aced20a97694434da09dc2ee9b8c19e4`.
  Both used the generated tied-chords fixture, reached ready, and retained source
  and companion hash checks. The native test installed the validated companion.
- Old Mac jobs remain on the Mac. Cross-principal job reads return 404.
  Public website deployment and the App Store release are separate work.
