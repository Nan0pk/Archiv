# Native InPage fixture acquisition

No lawful redistributable native fixture corpus was established by either independent research report. This document defines the acquisition kit required to unblock implementation.

## Provenance requirements

Every fixture must include:

- original native file;
- owner and creator;
- explicit repository and public-CI redistribution permission;
- creator application version/build and operating system when known;
- creation date when known;
- SHA-256 and byte size;
- declared language mix;
- Unicode text ground truth;
- PDF and page screenshots;
- expected page/story/frame relationships;
- human review notes for bidi order, joining, punctuation, numerals and normalization.

Use `schemas/native-inpage-fixture-manifest.schema.json` for the machine-readable record.

## Initial fixture matrix

Create controlled documents for:

1. Urdu-only, one page.
2. Arabic-only, one page.
3. Persian-only, one page.
4. Sindhi-only, one page.
5. Pashto/Pushto-only, one page.
6. Kashmiri-only, one page.
7. Mixed Urdu/English with both Latin and Perso-Arabic numerals.
8. Diacritics, joining marks, hamza variants and punctuation.
9. Multi-page continuous story.
10. Linked text frames across pages.
11. Multiple unlinked frames where creation order differs from visual order.
12. Multiple stories on one page.
13. Table or table-like layout.
14. Embedded image or non-text object.
15. Image-only document.
16. Protected/encrypted document, when the licensed version supports it.
17. Older and newer unsupported-version samples.
18. Split-document set, if available.
19. Corrupt/truncated derivatives made from owned fixtures.
20. Unrelated `.inp` and unrelated CFB negative files.

## Draft authoring text

These strings are prompts for fixture creation, not linguistic ground truth. Native speakers must review and freeze the final text.

### Urdu

`یہ ایک آزمائشی اُردو متن ہے: اعداد ۱۲۳ اور 123، سوال؟ جواب! قوسین (مثال)۔`

### Arabic

`هٰذا نَصٌّ عَرَبِيٌّ للاختبار: الأرقام ١٢٣ و123، والسؤال؟ والجواب!`

### Persian

`این یک متن آزمایشی فارسی است: اعداد ۱۲۳ و 123، نشانه‌گذاری، و جهت نوشتار.`

### Sindhi

`هي سنڌيءَ جو آزمائشي متن آهي: ٻ، ڄ، ڳ، ڙ، ۽ انگ ۱۲۳.`

### Pashto/Pushto

`دا د پښتو ازمایښتي متن دی: ښ، ږ، ټ، ډ، ڼ، او شمېرې ۱۲۳.`

### Kashmiri

`یِہ چھُ کٲشُر آزمایشی متن: ژ، چھ، ٲ، ۄ، اعداد ۱۲۳۔`

### Mixed Urdu/English

`Archiv test: یہ mixed متن ہے — email test@example.com، تاریخ 2026-08-02، اعداد ۱۲۳/123.`

## Controlled comparison pairs

For reverse engineering, create pairs differing in exactly one property:

- one Urdu word;
- one English word;
- one digit style;
- one punctuation mark;
- one diacritic;
- one paragraph;
- one page;
- one text frame;
- one linked-frame relationship;
- one font;
- one point size;
- one paragraph direction;
- one image;
- one application version.

Record full-file and per-stream hashes after bounded inspection.

## Negative fixtures

Generate negatives lawfully:

- rename plain text to `.inp`;
- use a small Abaqus-style `*Heading` input;
- create synthetic unrelated CFB files;
- remove or orphan candidate stream names;
- truncate owned native fixtures at controlled offsets;
- corrupt FAT, DIFAT and directory links in copies;
- mutate length/type fields only in disposable copies;
- exceed parser limits with generated synthetic files.

Never use private archives, pirated software, copyrighted publishing files without permission, or malware samples as normal regression fixtures.

## Permission declaration

Each contributed fixture bundle must include a signed or attributable statement covering:

- ownership or authorization to contribute;
- permission to redistribute the native file and ground-truth artifacts;
- permission for public CI processing and artifact retention;
- absence of private personal information;
- exact licensed InPage version used;
- whether fonts or embedded assets impose separate restrictions.

## Smallest external procedure

The smallest useful external contribution is one licensed-environment bundle containing:

- Urdu-only native `.inp`;
- mixed Urdu/English native `.inp`;
- multi-page linked-frame native `.inp`;
- Unicode exports;
- PDFs and screenshots;
- creator version/build;
- redistribution declaration.

That bundle is sufficient to begin controlled stream comparison, but not sufficient to claim broad language or version support.
