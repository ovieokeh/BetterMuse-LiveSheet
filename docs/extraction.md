# Extraction scope

Source checkout: BetterMuse, base commit
`ba16299eb2d2865c6efddaf37b8f5bbdc793567d`, plus the existing uncommitted pipeline
hardening work. The per-file source hashes in `extraction-manifest.json` identify
that actual working-tree snapshot; the base commit alone does not reproduce it.

The split moves reusable code into an installable `livesheet` package. It adds
standalone entry points, portable storage/tool configuration, pinned asset
bootstrap, the runtime/test images, and local verification tools.

The catalogue-only command entry points and bundled-example tempo overrides were
removed from the extracted modules. They remain in BetterMuse for its catalogue
workflows. All uploaded-PDF recognition and repair decisions retain their existing
behavior. The old global exporter compatibility bridge remains removed.

Package names and code paths change algorithm fingerprints. Do not relabel an old
cache as new. The full extraction comparison instead checks resulting musical
events against frozen pre-extraction outputs and independent references.

The new repository contains no website/app code, private PDFs, job history,
credentials, tool binaries, or BetterMuse build folders. It contains the minimal
public/generated fixtures needed for an independent regression suite.

The previous pipeline copy remains with the existing local website/device service
as a rollback checkpoint. The cutover steps are in `local-integration.md`. This
extraction does not deploy, publish, remove old uploads, or migrate app settings.
