# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Name attribution and evidence citation for face clusters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from PIL import ExifTags, Image, IptcImagePlugin

from archiv.faces.config import check_faces_opt_in
from archiv.faces.contracts import CandidateName, ClusterAttribution, EvidenceCitation
from archiv.faces.storage import connect_face_index, face_index_path, get_confirmation
from archiv.storage.layout import ArchivLayout

_NOISE_WORDS = {
    "img",
    "image",
    "pic",
    "picture",
    "photo",
    "portrait",
    "headshot",
    "scan",
    "dsc",
    "crop",
    "cropped",
    "face",
    "snapshot",
    "untitled",
    "screenshot",
    "thumbnail",
    "preview",
    "of",
    "the",
    "and",
}


def _clean_candidate_name(raw: str) -> str | None:
    """Normalize and validate a string candidate name."""
    cleaned = re.sub(r"[_\-\.]+", " ", raw).strip()
    # Strip any numeric suffixes or prefixes (e.g. "John Smith 2024" or "01 Alice")
    cleaned = re.sub(r"\b\d+\b", "", cleaned).strip()
    words = [w for w in cleaned.split() if w.lower() not in _NOISE_WORDS]
    if not (1 <= len(words) <= 4):
        return None
    for w in words:
        if not re.match(r"^[A-Za-z]+['-]?[A-Za-z]*$", w):
            return None
    # Capitalize into standard Title Case
    formatted = " ".join(w.capitalize() for w in words)
    if len(formatted) < 3:
        return None
    return formatted


def extract_from_filename(filename: str) -> str | None:
    """Extract person name hypothesis from filename."""
    stem = Path(filename).stem
    return _clean_candidate_name(stem)


def extract_from_exif(image_path: Path) -> list[tuple[str, str]]:
    """Extract candidate name and detail from image EXIF metadata."""
    results: list[tuple[str, str]] = []
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return results
            for tag_id, value in exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                if tag_name in {"Artist", "XPAuthor", "CameraOwnerName"}:
                    if isinstance(value, bytes):
                        val_str = value.decode("utf-8", errors="replace").strip("\x00")
                    elif isinstance(value, str):
                        val_str = value.strip()
                    else:
                        continue
                    cleaned = _clean_candidate_name(val_str)
                    if cleaned:
                        results.append((cleaned, f"EXIF {tag_name}: {val_str}"))
    except Exception:
        pass
    return results


def extract_from_iptc(
    image_path: Path,
) -> list[tuple[str, str, Literal["iptc", "caption"]]]:
    """Extract candidate name and detail from image IPTC metadata."""
    results: list[tuple[str, str, Literal["iptc", "caption"]]] = []
    try:
        with Image.open(image_path) as img:
            iptc_info = IptcImagePlugin.getiptcinfo(img)
            if not iptc_info:
                return results
            # Check By-line: (2, 80)
            byline_val = iptc_info.get((2, 80))
            if byline_val:
                vals = byline_val if isinstance(byline_val, list) else [byline_val]
                for v in vals:
                    s = v.decode("utf-8", errors="replace").strip()
                    cleaned = _clean_candidate_name(s)
                    if cleaned:
                        results.append((cleaned, f"IPTC By-line: {s}", "iptc"))

            # Check Caption/Abstract: (2, 120)
            caption_val = iptc_info.get((2, 120))
            if caption_val:
                caps = caption_val if isinstance(caption_val, list) else [caption_val]
                for c in caps:
                    s = c.decode("utf-8", errors="replace").strip()
                    # Check for "Photo of <Name>" or simple 2-word capitalized names
                    for match in re.finditer(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", s):
                        candidate = _clean_candidate_name(match.group(1))
                        if candidate:
                            results.append((candidate, f"IPTC Caption: '{s}'", "caption"))
    except Exception:
        pass
    return results


def attribute_cluster(
    layout: ArchivLayout,
    cluster_id: str,
    label: str | None = None,
) -> ClusterAttribution | None:
    """Build attribution hypotheses and citations for a face cluster."""
    db_path = face_index_path(layout)
    if not db_path.is_file():
        return None

    with connect_face_index(db_path) as conn:
        # Check cluster row
        row = conn.execute(
            "SELECT cluster_id, label, member_count FROM face_clusters WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchone()
        if not row:
            return None

        cluster_label = label or str(row["label"])
        member_count = int(row["member_count"])

        # Check confirmation
        confirmation = get_confirmation(conn, cluster_id)
        if confirmation:
            status: Literal["unconfirmed", "confirmed"] = "confirmed"
            confirmed_name, confirmed_at = confirmation
        else:
            status = "unconfirmed"
            confirmed_name, confirmed_at = None, None

        # Fetch member faces
        faces = conn.execute(
            "SELECT face_id, object_sha256, source_name FROM faces WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchall()

    # Collect citations for each candidate name
    supporting_by_name: dict[str, list[EvidenceCitation]] = {}

    for face in faces:
        digest = str(face["object_sha256"])
        source_name = str(face["source_name"])
        orig_path = layout.original_path(digest)

        # 1. Filename citation
        fn_candidate = extract_from_filename(source_name)
        if fn_candidate:
            cit = EvidenceCitation(
                source_type="filename",
                source_name=source_name,
                object_sha256=digest,
                detail=f"Filename pattern in '{source_name}'",
                weight=0.70,
            )
            supporting_by_name.setdefault(fn_candidate, []).append(cit)

        # 2. Derived document check if available
        derived_doc_path = layout.derived_root(digest) / "normalized" / "document.json"
        if derived_doc_path.is_file():
            try:
                doc_data = json.loads(derived_doc_path.read_text(encoding="utf-8"))
                metadata = doc_data.get("metadata", {})
                image_meta = metadata.get("image", {})

                # Check EXIF in derived metadata
                exif_dict = image_meta.get("exif", {})
                for tag in ("Artist", "XPAuthor", "CameraOwnerName"):
                    val = exif_dict.get(tag)
                    if isinstance(val, str):
                        c = _clean_candidate_name(val)
                        if c:
                            cit = EvidenceCitation(
                                source_type="exif",
                                source_name=source_name,
                                object_sha256=digest,
                                detail=f"EXIF {tag}: {val}",
                                weight=0.85,
                            )
                            supporting_by_name.setdefault(c, []).append(cit)

                # Check IPTC in derived metadata
                iptc_dict = image_meta.get("iptc", {})
                byline = iptc_dict.get("(2, 80)") or iptc_dict.get("by-line")
                if byline:
                    bl_vals = byline if isinstance(byline, list) else [byline]
                    for bv in bl_vals:
                        if isinstance(bv, str):
                            c = _clean_candidate_name(bv)
                            if c:
                                cit = EvidenceCitation(
                                    source_type="iptc",
                                    source_name=source_name,
                                    object_sha256=digest,
                                    detail=f"IPTC By-line: {bv}",
                                    weight=0.90,
                                )
                                supporting_by_name.setdefault(c, []).append(cit)

                # Check text segments for captions
                for seg in doc_data.get("segments", []):
                    seg_text = seg.get("text", "")
                    if seg_text:
                        for match in re.finditer(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", seg_text):
                            cand = _clean_candidate_name(match.group(1))
                            if cand:
                                cit = EvidenceCitation(
                                    source_type="caption",
                                    source_name=source_name,
                                    object_sha256=digest,
                                    detail=f"Document text caption: '{seg_text[:80]}'",
                                    weight=0.75,
                                )
                                supporting_by_name.setdefault(cand, []).append(cit)
            except Exception:
                pass

        # 3. Direct image file inspection (for direct EXIF/IPTC tags)
        if orig_path.is_file():
            exif_candidates = extract_from_exif(orig_path)
            for cand, detail in exif_candidates:
                cit = EvidenceCitation(
                    source_type="exif",
                    source_name=source_name,
                    object_sha256=digest,
                    detail=detail,
                    weight=0.85,
                )
                supporting_by_name.setdefault(cand, []).append(cit)

            iptc_candidates = extract_from_iptc(orig_path)
            for cand, detail, stype in iptc_candidates:
                cit = EvidenceCitation(
                    source_type=stype,
                    source_name=source_name,
                    object_sha256=digest,
                    detail=detail,
                    weight=0.90 if stype == "iptc" else 0.75,
                )
                supporting_by_name.setdefault(cand, []).append(cit)

    # Score candidates
    candidates: list[CandidateName] = []
    for cand_name, citations in supporting_by_name.items():
        # Deduplicate citations by (source_type, source_name, detail)
        dedup_cits: list[EvidenceCitation] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for c in citations:
            key = (c.source_type, c.source_name, c.detail)
            if key not in seen_keys:
                seen_keys.add(key)
                dedup_cits.append(c)

        # Probabilistic confidence combination: 1 - prod(1 - w * 0.75)
        unconf = 1.0
        for c in dedup_cits:
            factor = min(0.95, c.weight * 0.75)
            unconf *= 1.0 - factor
        confidence = round(max(0.1, min(0.99, 1.0 - unconf)), 3)

        # Contradicting citations: citations pointing to other names in the same cluster
        contradictions: list[EvidenceCitation] = []
        for other_name, other_cits in supporting_by_name.items():
            if other_name != cand_name:
                for oc in other_cits[:2]:
                    contradictions.append(oc)

        candidates.append(
            CandidateName(
                name=cand_name,
                confidence=confidence,
                supporting_citations=dedup_cits,
                contradicting_citations=contradictions[:3],
            )
        )

    # Sort candidates by confidence descending
    candidates.sort(key=lambda c: c.confidence, reverse=True)

    return ClusterAttribution(
        cluster_id=cluster_id,
        label=cluster_label,
        member_count=member_count,
        status=status,
        confirmed_name=confirmed_name,
        confirmed_at=confirmed_at,
        candidates=candidates,
    )


def attribute_all_clusters(home: Path | None = None) -> list[ClusterAttribution]:
    """Retrieve attribution analysis for all face clusters."""
    check_faces_opt_in(home)
    layout = ArchivLayout.resolve(home)
    db_path = face_index_path(layout)
    if not db_path.is_file():
        return []

    with connect_face_index(db_path) as conn:
        rows = conn.execute(
            "SELECT cluster_id, label FROM face_clusters ORDER BY member_count DESC, created_at ASC"
        ).fetchall()

    attributions: list[ClusterAttribution] = []
    for r in rows:
        attr = attribute_cluster(layout, str(r["cluster_id"]), str(r["label"]))
        if attr is not None:
            attributions.append(attr)

    return attributions


def find_cluster_by_target(
    layout: ArchivLayout,
    target: str,
) -> tuple[str, str] | None:
    """Find (cluster_id, label) by cluster_id, label ('Person 1'), or confirmed name."""
    db_path = face_index_path(layout)
    if not db_path.is_file():
        return None

    target_str = target.strip()
    with connect_face_index(db_path) as conn:
        # Match exact cluster_id
        row = conn.execute(
            "SELECT cluster_id, label FROM face_clusters WHERE cluster_id = ?",
            (target_str,),
        ).fetchone()
        if row:
            return str(row["cluster_id"]), str(row["label"])

        # Match label case-insensitively (e.g. "Person 1" or "person 1")
        row = conn.execute(
            "SELECT cluster_id, label FROM face_clusters WHERE LOWER(label) = LOWER(?)",
            (target_str,),
        ).fetchone()
        if row:
            return str(row["cluster_id"]), str(row["label"])

        # Match confirmed name
        row = conn.execute(
            """
            SELECT c.cluster_id, fc.label
            FROM confirmations c
            JOIN face_clusters fc ON c.cluster_id = fc.cluster_id
            WHERE LOWER(c.confirmed_name) = LOWER(?)
            """,
            (target_str,),
        ).fetchone()
        if row:
            return str(row["cluster_id"]), str(row["label"])

    return None
