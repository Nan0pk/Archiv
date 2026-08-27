# Watermark/scan synthetic-generation contract

Generate a two-layer PDF from lawful neutral text: a native text layer containing only the ten
characters `WATERMARK!`, and a raster page body containing `SCAN-BODY-RECOVERY-515`. The regression
passes only when OCR recovers the body marker. This recipe avoids redistributing the private scan
that exposed the defect.
