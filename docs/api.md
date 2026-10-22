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
- No preparation-mode header is needed. All uploads use the repair pipeline.
  Older clients may still send `X-Preparation-Profile: experimental-v1`; this is
  accepted as a compatibility alias for the only pipeline. `standard` and unknown
  values are rejected. The retained `experimental-v1` string identifies output
  provenance; it no longer selects an optional mode.
- `Idempotency-Key` is optional for local web access. Reusing it with another PDF
  fails. A repeated key for an existing job returns that job unchanged, even if
  it was prepared by the retired pipeline. The opt-in native and hosted pilot
  access paths require a UUID-shaped key.

States are `queued`, `running`, `ready`, `failed`, and `cancelled`. Polling reports
stage progress. Clients must preserve the readable original if preparation fails.
Do not infer readiness from a file appearing on disk or from elapsed time.

## Limits and recovery

- 25 MiB, 20 pages per PDF; encrypted/invalid PDFs fail inspection.
- At most 10 unfinished jobs, with at most two recognition workers (default two; `--workers 1` is supported).
- 40-minute worker deadline; process-group cancellation.
- 2 GiB disk per running job, including its temporary files and compiler cache;
  8 GiB total store; 10 GiB free-space floor. These are disk limits, not RAM limits.
- Finished jobs and quarantine records expire after seven days.
- Interrupted work is requeued on service restart. Cancelled jobs stay cancelled.
- Cancellation/deletion targets only that job. Retry waits for the old attempt
  to stop and release its slot.
- Local `/health` retains `worker` and adds `workerCount` (1 or 2).
  Hosted `/health` exposes only `healthy`; detailed hosted metrics require an
  operator credential. See [hosted access and limits](hosted-pilot.md).
- Mutations during shutdown return HTTP 503 (`service_stopping`).
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
- `quality.json`: repair evidence and validation report.
- Baseline artifacts are intermediate diagnostic evidence, not a second playback mode.
- `worker.log`: bounded diagnostics for local investigation.

`result.json` and `completion.json` are internal files. The latter uses schema 1
and hashes the exact original, score, companion, metadata and page images. It is
written last and validated by the service. The public job result supplies the
source and companion hashes for client-side identity checks.

A restart can still serve older ready jobs without a completion marker for
backward compatibility. Newly prepared jobs require a valid marker. A valid
manifest proves integrity, not musical correctness. `musicalReview` remains
`unreviewed` unless separately reviewed.

Retries and resumed unfinished jobs use the current pipeline. Existing ready jobs
remain unchanged. A repair validation error fails preparation instead of returning
the earlier output. Individual repair stages may still decline unsupported edits
when their evidence is insufficient; that preserves their input for later validation.
