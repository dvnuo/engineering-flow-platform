from .capabilities import can_project_to_text, infer_projection_kind, is_text_like_content_type
from .models import ArtifactBinding, ArtifactRecord
from .service import (
    attach_source_refs_to_artifact,
    attach_text_ref_to_artifact,
    bind_artifact_to_session,
    bind_artifact_to_source_bundle,
    build_artifact_ref_dict,
    register_existing_file_as_artifact,
    update_projection_from_parse_result,
)
from .storage import storage

__all__ = [
    "ArtifactBinding",
    "ArtifactRecord",
    "storage",
    "can_project_to_text",
    "is_text_like_content_type",
    "infer_projection_kind",
    "register_existing_file_as_artifact",
    "attach_text_ref_to_artifact",
    "attach_source_refs_to_artifact",
    "bind_artifact_to_session",
    "bind_artifact_to_source_bundle",
    "update_projection_from_parse_result",
    "build_artifact_ref_dict",
]
