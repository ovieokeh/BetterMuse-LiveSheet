# Working on LiveSheet

- Keep work local unless publication or deployment is explicitly requested.
- Explain outcomes first. Use short sentences and plain language.
- Preserve the app companion and website score contracts.
- Keep recognition rules generic and supported by evidence in the PDF.
  Never branch on score titles, filenames, job IDs, or known measure numbers.
- Keep source PDFs immutable. Repairs produce separate candidates.
- Do not weaken validation, rewrite reference answers to match recognition, or
  treat existing mismatches as successful musical interpretations.
- Use the explicit stage registry for applicable repairs. Document exceptions.
- Add targeted regression and refusal cases for musical changes. Run the
  generated corpus and private reviewed cases when relevant and available.
- Keep private PDFs, their derived snippets, uploads and tool downloads out of Git
  and images. Private tests must report skips when data is unavailable.
- Use `data/` for service storage, `artifacts/` for evidence, `.build/` for temporary
  work. Keep the 10 GiB storage floor. Do not delete unrelated project caches.
- Runtime code lives in `livesheet/`; test helpers live in `livesheet/testing/`.
  Do not add runtime imports from the test helper package.
- Before committing, check for credentials, private paths, unexpected large files,
  source changes outside the requested scope, and accidental network exposure.
