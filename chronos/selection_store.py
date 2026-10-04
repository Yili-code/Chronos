"""Durable explicit selections with optimistic revision checks."""
from dataclasses import asdict, replace
from datetime import datetime
import re
from .study_materials import MaterialSelection, PdfMaterial


def encode(selection):
    data = asdict(selection)
    data["selected_ids"] = sorted(selection.selected_ids)
    data["catalog"] = [{**asdict(item), "uploaded_at": item.uploaded_at.isoformat() if item.uploaded_at else None}
                       for item in selection.catalog]
    return data


def decode(data):
    return MaterialSelection(**{**data, "selected_ids": frozenset(data["selected_ids"]),
        "catalog": tuple(PdfMaterial(**{**item, "uploaded_at": datetime.fromisoformat(item["uploaded_at"]) if item["uploaded_at"] else None})
                         for item in data["catalog"])})


class SelectionStore:
    def __init__(self, db):
        self.db = db

    def bind_execution(self, key, chat_id, *, model, prompt_version, pagination_version):
        """Pin generation configuration transactionally; never reset on deployment."""
        self.validate_key(key)
        binding = dict(model=model, prompt_version=prompt_version, pagination_version=pagination_version)
        if any(not isinstance(value, str) or not value.strip() for value in binding.values()):
            raise ValueError("execution configuration required")
        def transition(previous):
            if previous is None or previous['chat_id'] != chat_id or not previous['selection']['confirmed']:
                raise ValueError("confirmed owner selection required")
            existing = previous.get('execution')
            if existing is not None:
                if existing != binding:
                    raise ValueError("execution configuration changed")
                return previous
            if previous.get('processing_status') not in (None, 'deferred_attachment', 'selection_binding_required'):
                raise ValueError("legacy execution requires review")
            return {**previous, 'execution': binding}
        return self.db.mutate_material_selection(key, transition)

    @staticmethod
    def validate_key(key):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", key):
            raise ValueError("invalid selection key")

    def create(self, key, chat_id, selection):
        self.validate_key(key)
        state = {"chat_id": chat_id, "revision": 0, "message_id": None, "selection": encode(selection)}
        def transition(previous):
            if previous is not None:
                if previous["chat_id"] != chat_id or previous["selection"]["session_id"] != selection.session_id:
                    raise ValueError("selection identity conflict")
                return previous
            return state
        return self.db.mutate_material_selection(key, transition)

    def bind_message(self, key, chat_id, message_id):
        self.validate_key(key)
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("invalid selection message")
        def transition(previous):
            if previous is None or previous["chat_id"] != chat_id:
                raise ValueError("selection not available")
            if previous.get("message_id") not in {None, message_id}:
                raise ValueError("selection already bound")
            return {**previous, "message_id": message_id}
        return self.db.mutate_material_selection(key, transition)

    def freeze_content(self, key, chat_id, verified):
        """Bind content identities once without changing the confirmed choices."""
        self.validate_key(key)
        def transition(previous):
            if previous is None or previous["chat_id"] != chat_id:
                raise ValueError("selection not available")
            current = decode(previous["selection"])
            if not current.confirmed or not verified.confirmed:
                raise ValueError("confirmed selection required")
            original_shape = replace(current, catalog=tuple(replace(item, sha256=None) for item in current.catalog))
            verified_shape = replace(verified, catalog=tuple(replace(item, sha256=None) for item in verified.catalog))
            if original_shape != verified_shape:
                raise ValueError("selection context changed")
            items = []
            for old, new in zip(current.catalog, verified.catalog):
                if old.source_id in current.selected_ids:
                    if new.sha256 is None or (old.sha256 is not None and old.sha256 != new.sha256):
                        raise ValueError("selected content identity changed")
                    items.append(new)
                else:
                    items.append(old)
            return {**previous, "selection": encode(replace(current, catalog=tuple(items)))}
        return self.db.mutate_material_selection(key, transition)

    def apply(self, key, chat_id, revision, *, source_id=None, selected=None, confirm=False):
        self.validate_key(key)
        def transition(previous):
            if previous is None or previous["chat_id"] != chat_id:
                raise ValueError("selection not available")
            if previous["revision"] != revision:
                return previous  # A duplicate or stale button cannot change state.
            selection = decode(previous["selection"])
            if selection.confirmed:
                return previous
            if confirm:
                selection = selection.confirm()
            else:
                if type(selected) is not bool:
                    raise ValueError("explicit selection membership required")
                selection = selection.choose(source_id, selected=selected)
            return {**previous, "revision": revision + 1, "selection": encode(selection)}
        return self.db.mutate_material_selection(key, transition)
