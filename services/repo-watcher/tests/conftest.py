# services/repo-watcher/tests/conftest.py
import subprocess
import pytest


@pytest.fixture()
def sample_repo(tmp_path):
    """Create a minimal real git repo with one Python file and one initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

    auth_py = repo / "auth.py"
    auth_py.write_text(
        "import hashlib\n\n"
        "class AuthService:\n"
        "    def authenticate(self, username: str, password: str) -> str:\n"
        "        return hashlib.sha256(password.encode()).hexdigest()\n"
    )

    subprocess.run(["git", "add", "auth.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

    return repo
