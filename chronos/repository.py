"""Narrow repository contracts shared by the task and runtime boundaries."""

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskRepository(Protocol):
    def initialize(self) -> None: ...

    def check_ready(self) -> None: ...

    def create_task(
        self,
        title: str,
        due_at: datetime | None,
        project: str | None,
        created_at: datetime,
        timing: dict | None = None,
    ) -> dict: ...

    def list_open_tasks(self) -> list[dict]: ...

    def complete_task(self, task_id: int, completed_at: datetime) -> bool: ...

    def postpone_task(self, task_id: int, due_at: datetime) -> bool: ...

    def edit_task(
        self,
        task_id: int,
        title: str,
        due_at: datetime | None,
        project: str | None,
        timing: dict | None = None,
    ) -> bool: ...

    def clear_tasks(self) -> int: ...

    def list_tag_aliases(self) -> dict[str, str]: ...

    def save_tag_alias(
        self, project: str, alias: str, existing_aliases: dict[str, str] | None = None
    ) -> None: ...


TASK_REPOSITORY_METHODS = frozenset(
    name for name in TaskRepository.__dict__ if not name.startswith("_")
)


def missing_task_repository_methods(repository: object) -> set[str]:
    return {name for name in TASK_REPOSITORY_METHODS if not callable(getattr(repository, name, None))}
