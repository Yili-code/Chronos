"""Durable explicit selections with optimistic revision checks."""
from dataclasses import asdict
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

    @staticmethod
    def validate_key(key):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", key):
            raise ValueError("invalid selection key")

    def create(self, key, chat_id, selection):
        self.validate_key(key)
        state = {"chat_id": chat_id, "revision": 0, "selection": encode(selection)}
        def transition(previous):
            if previous is not None:
                if previous["chat_id"] != chat_id or previous["selection"]["session_id"] != selection.session_id:
                    raise ValueError("selection identity conflict")
                return previous
            return state
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
