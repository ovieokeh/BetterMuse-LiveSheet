# Local integration and handoff

LiveSheet owns the extracted worker and service implementation. The BetterMuse
app, website, score library, catalogue import commands and their existing assets
remain in BetterMuse. This split does not change their playback contracts.

## This Mac

The existing BetterMuse preview service already uses port 3176 and has an opt-in
native device listener. It remains running so the website, Trill and old listening
links are not disrupted during extraction.

The standalone container uses **127.0.0.1:3186** through a local ignored `.env`.
Its storage is the new repository's `data/`. It does not share or delete the old
service's uploads. The default Compose configuration still uses port 3176 on a
clean machine.

This is a side-by-side verification checkpoint. Existing website/app uploads
continue to use the original service until their explicit cutover. The old source
copy is retained for those clients and rollback. New pipeline development belongs
here; do not independently tune both copies.

## Website cutover

The current website proxy targets `http://127.0.0.1:3176`. No score format or player
change is needed to use this service:

1. Stop submissions and wait for active old jobs to finish or cancel them.
2. Stop the old service. Back up its storage. Never run two services on one store.
3. Either start with a fresh LiveSheet store, or copy finished job directories
   while both services are stopped. Do not copy device credentials into Git.
   Original files, job IDs and completion manifests must stay unchanged.
4. Set `LIVESHEET_PORT=3176` and start the container. Check `/health` and existing
   jobs through the website's `/api/local-pdf/` proxy.
5. Prepare a fresh generated PDF, verify downloads and listen before retiring the
   backup. Roll back by stopping the container and restarting the old service.

The old native LAN listener is not exposed by default Compose. Its later cutover
needs a deliberate network/authentication choice. Containerization alone does
not authorize making the API reachable from the internet or changing Trill.

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
