"""Biometric-compliant face detection, clustering, and identity resolution."""

from archiv.faces.attributions import (
    attribute_all_clusters,
    attribute_cluster,
    find_cluster_by_target,
)
from archiv.faces.clustering import scan_and_cluster_faces
from archiv.faces.config import (
    BiometricsDisabledError,
    check_faces_opt_in,
    load_face_config,
    save_face_config,
)
from archiv.faces.contracts import (
    CandidateName,
    ClusterAttribution,
    EvidenceCitation,
    FaceCluster,
    FaceConfig,
    FaceDetection,
)
from archiv.faces.detector import compute_face_embedding, detect_faces_in_image
from archiv.faces.storage import (
    confirm_cluster_name,
    forget_face_data,
    list_clusters,
    revoke_cluster_confirmation,
)

__all__ = [
    "BiometricsDisabledError",
    "CandidateName",
    "ClusterAttribution",
    "EvidenceCitation",
    "FaceCluster",
    "FaceConfig",
    "FaceDetection",
    "attribute_all_clusters",
    "attribute_cluster",
    "check_faces_opt_in",
    "compute_face_embedding",
    "confirm_cluster_name",
    "detect_faces_in_image",
    "find_cluster_by_target",
    "forget_face_data",
    "list_clusters",
    "load_face_config",
    "revoke_cluster_confirmation",
    "save_face_config",
    "scan_and_cluster_faces",
]
