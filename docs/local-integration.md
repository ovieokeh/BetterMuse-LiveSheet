# Local integration and handoff

LiveSheet owns the extracted worker and service implementation. The BetterMuse
app, website, score library, catalogue import commands and their existing assets
remain in BetterMuse. This split does not change their playback contracts.

## Active hosted test clients — 22 October 2026

The website test proxy and BetterMuse Dev now call the restricted HTTPS pilot.
See [hosted pilot](hosted-pilot.md) and
[client setup](../../BetterMuse/docs/local-pdf-preparation.md).
The website itself remains local. The public website and App Store build have
not been deployed or enabled for uploads. No existing local PDFs were migrated.

## Optional local setup (retained for pipeline development)

The website and development app can still use this standalone local container.
The Mac service from the original repository is stopped.

- Website: the existing Next.js proxy calls `http://127.0.0.1:3176`.
- Trill: the existing development connection uses `http://phoenix.local:3177`.
- Storage: `data/jobs/`. Shared seed cache: `data/cache/`. Each active attempt has
  private settings and compiler caches in disposable `data/jobs/runtime-*` folders.
- Two active conversions by default; additional uploads wait in the persistent queue.
- Device key: `data/device-token`, ignored by Git, mode 0600.
- The original job IDs, device key, owner identifiers and client URLs are unchanged.
  App checkpoints and old browser listening links therefore retain their identity.

The ignored `.env` records this Mac's user/group IDs, port 3176, private IPv4
address and local hostname. It contains no credential. Trill and the Mac must
share the network. The Mac and Docker must remain running.

## Start and stop

Default Compose exposes only the website API on loopback. Device access is an
explicit override. From this repository, with the local settings and key present:

```sh
docker compose -f compose.yaml -f compose.device.yaml up -d --no-build
docker compose -f compose.yaml -f compose.device.yaml logs -f livesheet
docker compose -f compose.yaml -f compose.device.yaml stop
```

BetterMuse's `scripts/local_pdf_device.py serve --host <private-ip>` starts that
same container. `launch --device Trill` opens BetterMuse Dev with the retained
connection. Neither helper launches the old Python implementation anymore.

If Wi-Fi assigns the Mac a different private address, update
`LIVESHEET_NATIVE_HOST` in `.env`, then recreate the container with the same two
Compose files. The app keeps using the local hostname. Do not rotate the key:
it identifies existing device jobs as well as authenticating requests.

The container binds port 3177 internally on all interfaces. Docker publishes it
only on the explicitly configured private host address. The API still requires
the expected Host header and bearer key. Browser-origin requests are refused.
The unauthenticated website API remains published only on 127.0.0.1. This is a
trusted local-network development setup, not a public HTTPS service.

For a fresh machine, choose its private address and `.local` hostname. Create a
new random 32-byte hex device key in `data/device-token` with mode 0600, owned by
the configured container user. Never copy credentials into Git or an image.
Existing installations must migrate their existing key instead.

## Migration and rollback

The cutover copied 38 terminal jobs from BetterMuse's
`artifacts/local-pdf-preparation/`. Both services were stopped during copying.
Every file was hashed before and after copying. The original store remains
untouched as a rollback copy; it is no longer the active store. The two generated
LiveSheet verification jobs were retained too. Seven-day retention still applies
in the active service.

Local evidence is in `artifacts/cutover-migration.json` and
`artifacts/cutover-clients.json`. These reports contain job identifiers and are
ignored by Git. Private scores and credentials are not part of the repository.

To roll back the service, stop this container first. For the pre-extraction Mac service, restart `scripts/local_pdf_server.py` in BetterMuse using its original storage,
private host, hostname and `--native-token-file`. Never run two writers on one
store or bind both services to the same ports.

The old store is a snapshot, not a synchronized backup. Jobs uploaded after the
cutover exist only in LiveSheet. Preserve them before any rollback. Never copy
running jobs between stores. Recheck hashes and ownership when merging finished
jobs, and refuse conflicting job IDs.

## Client behavior

All new app and website uploads use the same repair pipeline. Neither client
needs a mode header. The old `standard` mode and the website's version switch
have been removed. The provenance string `experimental-v1` is retained for
compatibility with existing artifacts; it is not a selectable mode.

Existing ready jobs remain unchanged. Idempotent retries of an upload request
return the existing job. Explicit retries of failed/cancelled jobs and resumed
unfinished jobs run the current pipeline. Validation failures fail preparation;
intermediate output remains diagnostic evidence only.

## Repository boundaries

The new runtime contains reusable recognition, repair, web-export and native
companion helpers. BetterMuse catalogue CLI entry points and editorial tempo
suggestions are excluded. Upload preparation never used those catalogue-only
suggestions.

`extraction-manifest.json` maps each original file to its new path and records both
hashes. Changes are package imports, portable tool/storage paths, package entry
points, and removal of catalogue-only commands. The worker's musical rules and
validation thresholds remain unchanged. Source-path fingerprints necessarily
change, so old cached recognition is not assumed equivalent without validation.

## Historical cutover verification — 2026-10-21

This records the service move before the subsequent single-pipeline change.

- 22 focused service/configuration tests passed on both macOS and Linux.
- All 38 migrated jobs retained their state. Downloaded source PDFs and ready
  web/native outputs matched the original hashes. Cross-owner access was refused.
- A fresh website upload used the repair profile. A fresh authenticated upload
  matched the native client's standard profile. Both completed and their output
  manifests and downloaded bytes verified. Native retry reused the same job ID.
- Both fresh jobs remained ready after a container restart. The installed service
  and worker source matched this checkout.
- Three existing website request-boundary tests passed.
- BetterMuse Dev launched on Trill with its retained connection. No app binary
  changed. A new physical-device import/listening review is still a manual check;
  the API exercise above was driven from this Mac.

The local runtime image was updated from the previously verified standalone
image, replacing only the service module. Pinned tools and musical code were
unchanged. This avoided rebuilding the toolchain while disk space was limited.
The canonical Dockerfile includes the same updated module on a clean build.
