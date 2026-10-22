# BetterMuse LiveSheet

PDF-to-playback preparation for BetterMuse. The service accepts a printed
music PDF and produces the existing web score and native companion formats.
It does not render the app UI or implement practice grading.

Recognition can still miss or misread music. A ready job means the output bundle
passed its structural and integrity checks. It is not a musical accuracy claim.

## Current setup and documentation

The restricted hosted pilot runs at `https://livesheet.bettermuse.app` on
`k3o-runner`, managed through Coolify. BetterMuse Dev and the local website test
client use it. The public website and App Store app have not been enabled for
hosted uploads. The local container remains available for development.

- [Architecture](docs/architecture.md): pipeline stages, tools, and code boundaries.
- [API and output contracts](docs/api.md): jobs, progress, files, and validation.
- [App and website connections](../BetterMuse/docs/local-pdf-preparation.md):
  current client configuration, credential storage, testing, and local rollback.
- [Hosted pilot operations](docs/hosted-pilot.md): Hetzner/Coolify deployment,
  authentication, limits, monitoring, alerts, recovery, and remaining release work.
- [Verification](docs/verification.md): automated checks and musical references.
- [Memory](docs/memory-benchmark.md) and [concurrency](docs/concurrency-benchmark.md):
  measured resource use and worker scheduling.
- [Extraction history](docs/extraction.md) and [local integration](docs/local-integration.md):
  repository split, retained local setup, and migration history.

Client-document links assume the two repositories are sibling directories.
Credentials and private scores are excluded from documentation and Git.

## Start locally

Requires Docker with Compose, Python 3.11+, and at least 10 GiB free in the storage
filesystem. More headroom is recommended. The image targets Linux AMD64; Apple
Silicon runs it through Docker's emulation. Allow 4 GiB RAM and 2 CPU cores.

```sh
python3 tools/bootstrap.py
export LIVESHEET_UID=$(id -u) LIVESHEET_GID=$(id -g)
docker compose up -d --build
curl http://127.0.0.1:3176/health
```

Bootstrap downloads four pinned **public tool assets** and verifies their hashes.
Builds also fetch Python and Ubuntu packages. No scores or source code are
uploaded. Preparation requires no external services. Offline worker verification disables
network access entirely. Compose uses a normal Docker bridge so the host can
reach its loopback-only published port; outbound traffic is not firewall-blocked.

By default, the API is available only on the Mac's loopback interface.
The opt-in `compose.device.yaml` adds an authenticated private-LAN listener; see
[local integration](docs/local-integration.md). Job files and caches
live in `data/`, outside the image. The container runs as a non-root user with a
read-only root filesystem. Do not publish port 3176 on a public interface.

Port 3176 must be free. See [local integration](docs/local-integration.md) before
replacing an existing BetterMuse preview service.

```sh
docker compose logs -f livesheet
docker compose stop
```

Two conversions run at a time. Additional uploads wait in the persistent queue.
Use `--workers 1` when running the service directly to limit it to one conversion.
Each conversion has private tool settings, temporary files and a compiler cache.

Stopping preserves jobs. Starting again requeues interrupted work. The service
retains finished uploads for seven days, unless deleted sooner.

## Upload

```sh
curl -X POST http://127.0.0.1:3176/jobs \
  -H 'Content-Type: application/pdf' \
  -H 'X-Filename: Example.pdf' \
  --data-binary @example.pdf
```

Poll `/jobs/<id>` until `ready`, `failed`, or `cancelled`. Every upload runs the
source-backed repair pipeline used in the reviewed examples. There is no mode
switch. Repair/export validation failures fail the job; earlier intermediate
output is never silently delivered instead. User correction is not included.

See [API and output contract](docs/api.md), [architecture](docs/architecture.md),
and [verification](docs/verification.md).

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install .
docker build --platform linux/amd64 --target test -t bettermuse-livesheet-test:local .
python3 tools/test.py
.venv/bin/python tools/verify.py
```

The generated reference corpus is included. Private listening-review PDFs are
not. Their optional checks are documented separately and never silently counted
as passing when absent.

The configured origin is `https://github.com/ovieokeh/BetterMuse-LiveSheet.git`.
Source pushes do not redeploy the hosted pilot. It uses a verified image
transferred directly to its server. See LICENSE and
[third-party notices](THIRD_PARTY_NOTICES.md) before distribution.
