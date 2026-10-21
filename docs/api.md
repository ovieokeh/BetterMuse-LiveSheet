# Local API and output contract

Base URL: `http://127.0.0.1:3176`. The browser uses the BetterMuse website's existing
local proxy. Direct cross-origin browser access is rejected. The default API is
trusted-local access, not public multi-user authentication.

## Requests

| Method and path | Purpose |
| --- | --- |
| GET `/health` | Worker liveness, installed-tool presence and limits |
| POST `/jobs` | Raw PDF body; returns HTTP 202 and a job record |
| GET `/jobs` | List jobs visible to the current local access path |
| GET `/jobs/<id>` | State, progress, error or ready result metadata |
| POST `/jobs/<id>/cancel` | Stop queued/running work |
| POST `/jobs/<id>/retry` | Retry a failed or cancelled job |
| DELETE `/jobs/<id>` | Cancel if needed, then remove this upload and its outputs |
| GET `/jobs/<id>/files/<name>` | Download an allowlisted artifact |

Upload headers:

- `Content-Type: application/pdf` and a bounded `Content-Length` are required.
- `X-Filename` supplies a display name, not a filesystem path.
- `X-Preparation-Profile` is `standard` or `experimental-v1`.
- `Idempotency-Key` is optional for local web access. Reusing it with another PDF
  or profile fails. The opt-in native access path requires a UUID-shaped key.

States are `queued`, `running`, `ready`, `failed`, and `cancelled`. Polling reports
stage progress. Clients must preserve the readable original if preparation fails.
Do not infer readiness from a file appearing on disk or from elapsed time.

## Limits and recovery

- 25 MiB, 20 pages per PDF; encrypted/invalid PDFs fail inspection.
- At most 10 unfinished jobs, with one recognition worker.
- 40-minute worker deadline; process-group cancellation.
- 2 GiB per running job, 8 GiB total store, 10 GiB free-space floor.
- Finished jobs and quarantine records expire after seven days.
- Interrupted work is requeued on service restart.
- Corrupt job metadata is quarantined. Invalid completed output is rejected.

All existing limits are retained. Docker Desktop may report guest filesystem free
space differently from the Mac; monitor both rather than relying only on the
service's storage floor.

## Artifacts

Before readiness, `source.pdf` and `preview.webp` remain available. Ready jobs add:

- `score.json`: timed playback and page/measure/note geometry for the website.
- `companion.json`: existing native companion schema, including MusicXML notation
  and staff/expression data where supported. The app can reuse its importer and
  practice engine without embedding Audiveris.
- `page-N.webp`: prepared page images.
- `quality.json` and baseline artifacts when produced by the selected profile.
- `worker.log`: bounded diagnostics for local investigation.

`result.json` and `completion.json` are internal files. The latter uses schema 1
and hashes the exact original, score, companion, metadata and page images. It is
written last and validated by the service. The public job result supplies the
source and companion hashes for client-side identity checks.

A restart can still serve older ready jobs without a completion marker for
backward compatibility. Newly prepared jobs require a valid marker. A valid
manifest proves integrity, not musical correctness. `musicalReview` remains
`unreviewed` unless separately reviewed.
