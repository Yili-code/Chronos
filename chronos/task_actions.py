"""Durable, conditional undo for the most recent successful owner edit."""
from copy import deepcopy
from uuid import uuid4

from .assignments import from_record, owner_deadline_edit, to_record
from .task_timing import normalize_timing


class UndoConflict(ValueError):
    pass


def task_fields(task):
    return {name: task.get(name) for name in ("title", "due_at", "project")} | {
        "timing": task.get("timing") or {},
    }


class TaskActions:
    """Backend adapters keep task, linked assignment, alias and undo writes atomic."""

    def edit_task_with_undo(self, task_id, title, due_at, project, timing, tag_alias=None):
        token = uuid4().hex

        def edit():
            before = self._load_task_action(task_id)
            if before is None:
                return None
            after = deepcopy(before)
            aliases = dict(before["aliases"])
            after["task"] = {
                "title": title.strip(), "due_at": due_at.isoformat() if due_at else None,
                "project": project, "timing": normalize_timing(timing, due_at),
            }
            if (before["task"]["due_at"] != after["task"]["due_at"]
                    or before["task"]["timing"].get("due_date") != after["task"]["timing"].get("due_date")):
                for linked in after["assignments"].values():
                    linked["record"] = to_record(owner_deadline_edit(from_record(linked["record"]), due_at))
            alias_key = tag_alias[0] if tag_alias else None
            old_alias = before["aliases"].get(alias_key) if alias_key else None
            if tag_alias:
                after["aliases"][alias_key] = tag_alias[1]
            # Alias comparisons concern only the preference this edit changed.
            before["aliases"] = {alias_key: old_alias} if alias_key else {}
            after["aliases"] = {alias_key: tag_alias[1]} if alias_key else {}
            if before == after:
                return {"id": task_id, **after["task"]}
            self._write_task_action(task_id, after, aliases)
            self._save_task_action_state("undo", {
                "token": token, "task_id": task_id, "before": before, "after": after,
            })
            return {"id": task_id, **after["task"], "undo_token": token}

        return self._run_task_action(edit)

    def undo_task_edit(self, token=None):
        def undo():
            saved = self.get_task_action_state("undo")
            if not saved or saved.get("consumed"):
                raise UndoConflict("No edit is available to undo.")
            if token and token != saved["token"]:
                raise UndoConflict("This Undo button has expired. Only the latest edit can be undone.")
            current = self._load_task_action(saved["task_id"])
            if current is None:
                raise UndoConflict("The task is completed or deleted. Nothing was undone.")
            aliases = dict(current["aliases"])
            current["aliases"] = {key: current["aliases"].get(key) for key in saved["after"]["aliases"]}
            if current != saved["after"]:
                raise UndoConflict("The task or its linked data changed after this edit. Nothing was undone.")
            self._write_task_action(saved["task_id"], saved["before"], aliases)
            self._save_task_action_state("undo", {**saved, "consumed": True})
            return {"id": saved["task_id"], **saved["before"]["task"],
                    "restored_aliases": saved["before"]["aliases"]}

        return self._run_task_action(undo)

    def mutate_task_action_state(self, key, transition):
        def mutate():
            previous = self.get_task_action_state(key)
            state = transition(previous)
            if state != previous:
                self._save_task_action_state(key, state)
            return state
        return self._run_task_action(mutate)
