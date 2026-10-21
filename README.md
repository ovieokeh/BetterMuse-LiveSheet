# BetterMuse LiveSheet

Local PDF-to-playback preparation for BetterMuse. The service accepts a printed
music PDF and produces the existing web score and native companion formats.
It does not render the app UI or implement practice grading.

Recognition can still miss or misread music. A ready job means the output bundle
passed its structural and integrity checks. It is not a musical accuracy claim.

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

This repository is local-only at the extraction checkpoint. No remote repository,
registry push, or hosted service has been created. See LICENSE and
[third-party notices](THIRD_PARTY_NOTICES.md) before distribution.
