"""Check local inline Markdown file links without network access."""
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def main():
    # Include tracked files and new, non-ignored documentation during development.
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=ROOT, check=True, capture_output=True,
    )
    errors = []
    links = 0
    paths = sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})
    for name in paths:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        # Code examples are not links; skip fenced blocks before examining prose.
        text = re.sub(r"(?ms)^```[^\n]*\n.*?^```[^\n]*$", "", text)
        text = re.sub(r"`[^`\n]+`", "", text)
        for match in re.finditer(r"\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", text):
            target = match.group(1).strip("<>")
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            links += 1
            destination = (ROOT if url.path.startswith("/") else path.parent) / unquote(url.path).lstrip("/")
            if not destination.exists():
                errors.append(f"{name}: missing local target {target}")
    for error in errors:
        print(error)
    print(f"Checked {links} local file links in {len(paths)} Markdown files; {len(errors)} missing targets.")
    # External URL availability and heading fragments need separate verification.
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
