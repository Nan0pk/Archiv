# Fixture provenance and licensing

All files are generated from synthetic constants by `scripts/generate_fixture_corpus.py`.
No personal, organisational, customer, classified, scraped, or proprietary material is used.

The fixtures have no separate licence and follow the repository's current default terms.
At introduction time Archiv was public but had not selected an open-source licence, so no
reuse rights should be inferred solely from public visibility.

The generator pins its document/image libraries in the development dependency set, fixes
Office metadata, normalizes ZIP ordering/timestamps/permissions, uses invariant PDF output,
and fixes image pixels and WAV samples. LibreOffice headless conversion was used as an
additional compatibility check for the generated DOCX, XLSX, PPTX, and PDF.
