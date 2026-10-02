"""Phase 2 PDF catalog and explicit selection rules; no browser credentials."""
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import re
from enum import Enum


class MaterialAvailability(str, Enum):
    LISTED = 'listed'
    VERIFIED = 'verified'
    DEFERRED = 'deferred_attachment'


@dataclass(frozen=True)
class PdfMaterial:
    source_id: str
    course_id: str
    filename: str
    uploaded_at: datetime | None
    sha256: str | None = None

    @property
    def availability(self) -> MaterialAvailability:
        return MaterialAvailability.VERIFIED if self.sha256 else MaterialAvailability.LISTED

    def __post_init__(self):
        if not self.source_id or not self.course_id:
            raise ValueError('material identity is required')
        if not self.filename.lower().endswith('.pdf'):
            raise ValueError('only PDF materials are supported')
        if self.uploaded_at is not None and self.uploaded_at.utcoffset() is None:
            raise ValueError('upload time must be timezone-aware')
        if self.sha256 is not None and not re.fullmatch(r'[0-9a-f]{64}', self.sha256):
            raise ValueError('invalid PDF content hash')


def course_catalog(course_id: str, materials: tuple[PdfMaterial, ...]) -> tuple[PdfMaterial, ...]:
    selected = tuple(item for item in materials if item.course_id == course_id)
    if len({item.source_id for item in selected}) != len(selected):
        raise ValueError('duplicate material identity')
    # Unknown dates are explicitly last; this is not a complete newest-first
    # guarantee unless every source supplies a genuine upload timestamp.
    return tuple(sorted(selected, key=lambda item: (
        item.uploaded_at is None,
        -item.uploaded_at.timestamp() if item.uploaded_at else 0,
        item.source_id,
    )))


@dataclass(frozen=True)
class MaterialSelection:
    session_id: str
    course_id: str
    reported_progress: str
    catalog: tuple[PdfMaterial, ...]
    selected_ids: frozenset[str] = frozenset()
    confirmed: bool = False

    def __post_init__(self):
        if not self.session_id or not self.reported_progress.strip():
            raise ValueError('course session and reported progress are required')
        if any(item.course_id != self.course_id for item in self.catalog):
            raise ValueError('catalog must belong to the selected course')
        ids = {item.source_id for item in self.catalog}
        if len(ids) != len(self.catalog) or not self.selected_ids <= ids:
            raise ValueError('invalid selection identities')
        if self.confirmed and not self.selected_ids:
            raise ValueError('confirm requires at least one PDF')

    def choose(self, source_id: str, *, selected: bool) -> 'MaterialSelection':
        """Set membership explicitly: duplicate callbacks must not toggle twice."""
        if self.confirmed:
            raise ValueError('confirmed selection is immutable')
        if source_id not in {item.source_id for item in self.catalog}:
            raise ValueError('material does not belong to this catalog')
        ids = self.selected_ids | {source_id} if selected else self.selected_ids - {source_id}
        return replace(self, selected_ids=frozenset(ids))

    def confirm(self) -> 'MaterialSelection':
        if not self.selected_ids:
            raise ValueError('select at least one PDF before confirming')
        return replace(self, confirmed=True)

    def generation_key(self, *, model: str, prompt_version: str) -> str:
        """Stable cache identity; a database claim is still needed for concurrency."""
        if not self.confirmed:
            raise ValueError('explicit confirmation is required before generation')
        if not model or not prompt_version:
            raise ValueError('generation configuration is required')
        if any(item.sha256 is None for item in self.catalog if item.source_id in self.selected_ids):
            raise ValueError('selected PDF content must be verified before generation')
        payload = {
            'session': self.session_id, 'course': self.course_id,
            'progress': self.reported_progress.strip(),
            'files': sorted((item.source_id, item.sha256) for item in self.catalog
                            if item.source_id in self.selected_ids),
            'model': model, 'prompt_version': prompt_version,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
