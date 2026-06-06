"""Admin cog helpers: the git-pull subprocess wrapper."""

import asyncio
import subprocess

from .constants import _REPO_ROOT


async def _git_pull():
    """Run `git pull` in a worker thread (network I/O — must not block the loop).

    Returns the completed process, or raises FileNotFoundError / TimeoutExpired.
    """

    def _run():
        return subprocess.run(
            ["git", "pull"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=_REPO_ROOT,
        )

    return await asyncio.to_thread(_run)
