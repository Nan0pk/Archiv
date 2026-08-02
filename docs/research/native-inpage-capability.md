# Native InPage capability research package

Status: research-only, no native `.inp` support claimed

## Purpose

This package consolidates two independent frontier-model investigations into a repository-grounded plan for issue #38. The reports were useful but not equally reliable:

- the first was discovery-heavy and could not access the repository or run code;
- the second independently verified the repository, corrected a significant negative-classification error, and supplied a practical fixture and probe design.

Neither report produced lawful native fixtures, a complete format specification, a verified native converter, or native text/layout measurements. Archiv therefore continues to reject `.inp`.

## Current verified boundary

- `.inp` is absent from the supported-format registry.
- PDF/TXT exports, OCR, clipboard mappings and generic CFB/OLE recognition are not native support.
- No legacy InPage application may be launched on untrusted input.
- No online converter may receive an Archiv document.
- No stream name establishes native text or layout support by itself.

The research probe in `src/archiv/research/inpage_cfb_probe.py` is deliberately outside ingestion dispatch. It validates bounded CFB structure and reports reachable root-level stream names without opening stream contents. Its strongest possible result is `inpage_cfb_candidate`.

## Findings retained

### Verified or strongly supported

1. Microsoft CFB is a general-purpose container with a file-system-like stream structure. Its header signature alone cannot identify the application format.
2. Public vulnerability analysis shows InPage processing an `InPage100` stream.
3. Crafted native InPage documents have been used for arbitrary-code-execution attacks.
4. CLE documents an InPage-to-Unicode converter and states that legacy InPage text uses a non-Unicode encoding scheme.
5. Inspected open-source mapping tools convert copied or supplied legacy text; they do not establish binary `.inp` parsing or native layout recovery.

### Discovery leads, not facts

- `DocumentInfo` as a companion stream is reported by community/file-identification sources but lacks an authoritative public format specification.
- `InPage200` and `InPage300` names are discovery leads. They are not mapped to product versions in Archiv.
- Split `.b01`/`.b02` documents are plausible but unsupported.
- Commercial or online conversion claims are not parser evidence and are unsuitable for private offline ingestion.

### Corrected error

One report initially treated a `2A 48` prefix as a possible non-CFB InPage family. The better-supported interpretation is that text beginning with `*Heading` is an unrelated Abaqus-style `.inp` input. It belongs in the negative fixture set, not the InPage classifier.

## What the probe proves

The probe can establish only:

- whether the file is structurally compatible with bounded CFB version 3 or 4 handling;
- which reachable directory streams are declared;
- whether root-level `DocumentInfo` and `InPageNNN` names coexist;
- whether the file is unrelated, malformed, oversized or merely a research candidate;
- the complete-file SHA-256.

It intentionally does not:

- read any stream bytes;
- decode text;
- infer an InPage product version;
- recover pages, stories, frames or objects;
- accept the file into canonical storage;
- modify the supported-format registry.

## External evidence still required

1. Lawful redistributable native fixtures from known licensed InPage versions.
2. Independent Unicode text ground truth.
3. PDF/screenshots and page/frame/story ground truth.
4. Comparative files differing by one controlled property.
5. Either enough record evidence for a bounded direct parser or a converter whose identity, license, versions, determinism, privacy and hostile-input behavior are verified.

## Repository artifacts

- `native-inpage-format-map.md`
- `native-inpage-converter-assessment.md`
- `native-inpage-fixture-acquisition.md`
- `native-inpage-decision.md`
- `native-inpage-evidence-register.json`
- `../../schemas/native-inpage-fixture-manifest.schema.json`
- `../../src/archiv/research/inpage_cfb_probe.py`
- `../../tests/test_inpage_cfb_probe.py`

## Sources

- [Microsoft Compound File Binary Format](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/53989ce4-7b05-4f8d-829b-d08d6148375b)
- [Microsoft CFB header](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/05060311-bfce-4b12-874d-71fd4ce63aea)
- [NVD CVE-2017-12824](https://nvd.nist.gov/vuln/detail/CVE-2017-12824)
- [Microsoft malicious InPage campaign analysis](https://www.microsoft.com/en-us/security/blog/2018/11/08/attack-uses-malicious-inpage-document-and-outdated-vlc-media-player-to-give-attackers-backdoor-access-to-targets/)
- [QiAnXin InPage100 analysis](https://ti.qianxin.com/blog/articles/analysis-of-targeted-attack-against-pakistan-by-exploiting-inpage-vulnerability-and-related-apt-groups-english/)
- [CLE InPage-to-Unicode converter](https://www.cle.org.pk/software/langproc/inpagetounicode.htm)
- [Existing Archiv evidence gate](../native-inpage-evidence-gate.md)
