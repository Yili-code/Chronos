import asyncio
import re
from pathlib import Path

import httpx


class ProjectService:
    def __init__(self, root: Path, github_token: str = ""):
        self.root = root.expanduser()
        self.github_token = github_token

    async def scan(self) -> list[dict]:
        if not self.root.exists():
            return []
        projects = [path for path in self.root.iterdir() if path.is_dir() and (path / ".git").exists()]
        return await asyncio.gather(*(self._inspect(path) for path in sorted(projects)))

    async def _git(self, path: Path, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git", "-C", str(path), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        return stdout.decode().strip() if process.returncode == 0 else ""

    async def _inspect(self, path: Path) -> dict:
        branch, changes, origin, last_commit = await asyncio.gather(
            self._git(path, "branch", "--show-current"),
            self._git(path, "status", "--porcelain"),
            self._git(path, "remote", "get-url", "origin"),
            self._git(path, "log", "-1", "--pretty=%h %s"),
        )
        project = {
            "name": path.name,
            "path": str(path),
            "branch": branch or "—",
            "dirty": bool(changes),
            "change_count": len(changes.splitlines()) if changes else 0,
            "origin": origin,
            "last_commit": last_commit or "尚無 commit",
        }
        repo = self._github_repo(origin)
        if repo and self.github_token:
            project["github"] = await self._github_status(repo)
        return project

    @staticmethod
    def _github_repo(origin: str) -> str | None:
        match = re.search(r"github\.com[/:]([^/]+/[^/.]+)(?:\.git)?$", origin)
        return match.group(1) if match else None

    async def _github_status(self, repo: str) -> dict:
        headers = {"Authorization": f"Bearer {self.github_token}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"https://api.github.com/repos/{repo}", headers=headers)
        if response.is_error:
            return {"repo": repo, "error": response.status_code}
        data = response.json()
        return {
            "repo": repo,
            "open_issues": data.get("open_issues_count", 0),
            "default_branch": data.get("default_branch"),
            "updated_at": data.get("updated_at"),
        }


def format_projects(projects: list[dict]) -> str:
    if not projects:
        return "找不到可追蹤的 Git repository。"
    lines = ["開發專案："]
    for project in projects:
        state = f"{project['change_count']} 項未 commit" if project["dirty"] else "乾淨"
        lines.append(f"• {project['name']}｜{project['branch']}｜{state}\n  {project['last_commit']}")
    return "\n".join(lines)

