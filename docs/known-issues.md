# Known issues

## Diagnostics export

- Schema `1` reports aggregate counts, not which document failed. This is intentional;
  support should suggest general recovery steps without requesting private source data.
- Platform and package versions may fingerprint an unusual installation, and aggregate
  counts disclose approximate library activity. Review the complete preview before saving.
- A destination must be a new file; the exporter refuses to overwrite an existing file.
- Database corruption is represented only as `unreadable`; raw SQLite errors are withheld.

## Image metadata and GPS coordinates

- EXIF metadata extraction preserves GPS coordinates when present in image headers.
  This extracted metadata is stored strictly within derived artifacts (`previews/metadata.json`)
  which are reconstructible and user-deletable.
- Diagnostics export (`archiv diagnostics-export`) operates strictly on aggregate system
  metrics and never includes document metadata, text segments, or GPS coordinates.

General alpha limitations remain documented in [Offline alpha](offline-alpha.md) and the
[format compatibility matrix](format-compatibility.json).
