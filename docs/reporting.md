# Cited DOCX reports

Archiv can turn validated local search results into a polished DOCX while preserving the boundary between generation and proof.

## Generate

```bash
archiv generate-report "MARKER" ./archiv-evidence-report.docx
```

Generation performs these steps:

1. search the rebuildable SQLite FTS5 index;
2. select distinct canonical sources;
3. revalidate every citation against the immutable original and normalized document;
4. build the report from Archiv's default template;
5. write inline numbered citations and a source appendix;
6. write a manifest containing the exact citation envelopes and DOCX hash;
7. independently validate the package and, by default, render it through LibreOffice.

The generated sidecars are:

- `<report>.docx.manifest.json` - report identity, query, DOCX hash, required sections, and complete citation evidence;
- `<report>.docx.validation.json` - structural, citation, PDF, extracted-text, and page-image validation evidence.

## Verify an existing report

```bash
archiv verify-report \
  ./archiv-evidence-report.docx \
  ./archiv-evidence-report.docx.manifest.json
```

Use `--no-render` only when LibreOffice and `pdftoppm` are intentionally unavailable. Structural and citation validation still run, but rendered-page evidence will not be produced.

## Success contract

A report is successful only when:

- the DOCX is a valid package with required Word members;
- required report sections are present;
- every inline citation appears beside its exact excerpt in a finding;
- every appendix entry contains its source name, locator, and segment identifier;
- every citation still resolves to the canonical original and exact normalized segment;
- the DOCX hash matches its manifest;
- source originals retain their content-addressed hashes;
- when rendering is enabled, LibreOffice produces a readable PDF;
- the rendered PDF contains all required sections and citations;
- each PDF page produces a nonblank page image.

Missing, malformed, uncited, stale, hash-mismatched, or blank-page output is reported as failed. The generator cannot override validator evidence.

## CI evidence

The `Office validation` workflow installs LibreOffice and Poppler on a GitHub-hosted runner, generates the synthetic representative corpus, ingests the text-bearing formats, rebuilds the search index, generates a cited report, renders it, and uploads:

- DOCX;
- manifest and validation JSON;
- rendered PDF;
- page PNGs.

No user documents, secrets, models, or self-hosted runners are involved.
