import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class GitReadError(Exception):
    """A safe explanation of an unreadable repository."""


class ProjectService:
    def __init__(self, root: Path, github_token: str = ""):
        self.root = root.expanduser()
        self.github_token = github_token

    async def scan(self) -> list[dict]:
        if not self.root.exists():
            return []
        projects = [path for path in self.root.iterdir() if path.is_dir() and (path / ".git").exists()]
        return await asyncio.gather(*(self._inspect(path) for path in sorted(projects)))

    async def _git(self, path: Path, *args: str, empty_exit_codes: tuple[int, ...] = ()) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                "git", "-C", str(path), *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "GIT_CEILING_DIRECTORIES": str(path.resolve().parent)},
            )
            stdout, stderr = await process.communicate()
        except OSError:
            raise GitReadError("無法執行 Git，請檢查安裝與存取權限。") from None
        if process.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
        if process.returncode in empty_exit_codes:
            return ""
        if b"dubious ownership" in stderr:
            raise GitReadError("Git 拒絕讀取：專案擁有者與執行帳號不同，請確認目錄信任設定。")
        raise GitReadError("Git 讀取失敗，請檢查專案完整性與存取權限。")

    async def _inspect(self, path: Path) -> dict:
        try:
            branch, changes, origin, head = await asyncio.gather(
                self._git(path, "branch", "--show-current"),
                self._git(path, "status", "--porcelain"),
                self._git(path, "remote", "get-url", "origin", empty_exit_codes=(2,)),
                self._git(path, "rev-parse", "--verify", "--quiet", "HEAD", empty_exit_codes=(1,)),
            )
            last_commit = await self._git(path, "log", "-1", "--pretty=%h %s") if head else "尚無 commit"
        except GitReadError as error:
            return {"name": path.name, "path": str(path), "branch": "未知",
                    "dirty": None, "change_count": None, "origin": None,
                    "last_commit": "無法讀取", "error": str(error)}
        project = {
            "name": path.name,
            "path": str(path),
            "branch": branch or "detached HEAD",
            "dirty": bool(changes),
            "change_count": len(changes.splitlines()) if changes else 0,
            "origin": origin,
            "last_commit": last_commit,
        }
        repo = self._github_repo(origin)
        if repo and self.github_token:
            project["github"] = await self._github_status(repo)
        return project

    @staticmethod
    def _github_repo(origin: str) -> str | None:
        scp = re.fullmatch(r"git@github\.com:(.+)", origin, re.IGNORECASE)
        if scp:
            path = scp.group(1)
        else:
            try:
                url = urlsplit(origin)
                if (url.scheme not in {"https", "http", "ssh", "git"}
                        or url.hostname != "github.com" or url.query or url.fragment):
                    return None
                path = url.path.removeprefix("/")
            except ValueError:
                return None
        path = path.removesuffix("/").removesuffix(".git")
        parts = path.split("/")
        if len(parts) != 2:
            return None
        owner, repo = parts
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", owner):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo) or repo in {".", ".."}:
            return None
        return f"{owner}/{repo}"

    async def _github_status(self, repo: str) -> dict:
        headers = {"Authorization": f"Bearer {self.github_token}", "Accept": "application/vnd.github+json"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"https://api.github.com/repos/{repo}", headers=headers)
        except httpx.TimeoutException:
            return {"repo": repo, "error": "timeout"}
        except httpx.RequestError:
            return {"repo": repo, "error": "connection_failed"}
        if response.is_error:
            return {"repo": repo, "error": response.status_code}
        try:
            data = response.json()
        except ValueError:
            return {"repo": repo, "error": "invalid_response"}
        if not isinstance(data, dict):
            return {"repo": repo, "error": "invalid_response"}
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
        if project.get("error"):
            lines.append(f"• {project['name']}｜狀態未知\n  {project['error']}")
            continue
        state = f"{project['change_count']} 項未 commit" if project["dirty"] else "乾淨"
        lines.append(f"• {project['name']}｜{project['branch']}｜{state}\n  {project['last_commit']}")
        if project.get("github", {}).get("error"):
            lines.append("  GitHub 資訊暫時無法取得；以上為本機狀態。")
    return "\n".join(lines)
