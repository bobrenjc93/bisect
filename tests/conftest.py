"""Pytest fixtures for bisect bot testing."""

from __future__ import annotations

import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import List

import pytest


@dataclass
class GitTestRepo:
    """A test git repository with helper methods."""
    
    path: Path
    commits: List[str]  # List of commit SHAs in order (oldest first)
    
    def get_commit_sha(self, index: int) -> str:
        """Get commit SHA by index (0 = first/oldest commit)."""
        return self.commits[index]
    
    @property
    def first_commit(self) -> str:
        """Get the first (oldest) commit SHA."""
        return self.commits[0]
    
    @property
    def last_commit(self) -> str:
        """Get the last (newest) commit SHA."""
        return self.commits[-1]


# Alias for backward compatibility
TestRepo = GitTestRepo


class GitRepoBuilder:
    """
    Helper class to create test git repositories with controlled commit history.
    
    Usage:
        builder = GitRepoBuilder(tmp_path)
        builder.init()
        builder.write_file("test.py", "print('hello')")
        builder.commit("Initial commit")
        builder.write_file("test.py", "print('goodbye')")
        builder.commit("Break things")
        repo = builder.build()
    """
    
    def __init__(self, base_path: Path):
        self.path = base_path / "test_repo"
        self.path.mkdir(parents=True, exist_ok=True)
        self.commits: List[str] = []
        self._initialized = False
    
    def _run_git(self, *args: str) -> subprocess.CompletedProcess:
        """Run a git command in the repo directory."""
        result = subprocess.run(
            ["git"] + list(args),
            cwd=self.path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git command failed: git {' '.join(args)}\n{result.stderr}")
        return result
    
    def init(self, initial_branch: str = "main") -> "GitRepoBuilder":
        """Initialize the git repository."""
        self._run_git("init", "-b", initial_branch)
        self._run_git("config", "user.email", "test@example.com")
        self._run_git("config", "user.name", "Test User")
        self._initialized = True
        return self
    
    def write_file(self, filename: str, content: str) -> "GitRepoBuilder":
        """Write content to a file in the repo."""
        filepath = self.path / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
        return self
    
    def delete_file(self, filename: str) -> "GitRepoBuilder":
        """Delete a file from the repo."""
        filepath = self.path / filename
        if filepath.exists():
            filepath.unlink()
        return self
    
    def commit(self, message: str) -> "GitRepoBuilder":
        """Stage all changes and commit with the given message."""
        self._run_git("add", "-A")
        self._run_git("commit", "-m", message, "--allow-empty")
        result = self._run_git("rev-parse", "HEAD")
        sha = result.stdout.strip()
        self.commits.append(sha)
        return self
    
    def build(self) -> GitTestRepo:
        """Build and return the GitTestRepo."""
        return GitTestRepo(path=self.path, commits=self.commits.copy())


@pytest.fixture
def git_repo_builder(tmp_path: Path):
    """
    Fixture that provides a GitRepoBuilder for creating test repositories.
    
    Usage:
        def test_something(git_repo_builder):
            repo = (git_repo_builder
                .init()
                .write_file("test.sh", "#!/bin/bash\\nexit 0")
                .commit("Initial")
                .build())
    """
    return GitRepoBuilder(tmp_path)


@pytest.fixture
def simple_test_repo(tmp_path: Path) -> GitTestRepo:
    """
    Create a simple test repository with a test script that can be broken.
    
    This creates a repo with:
    - Commit 0: Initial setup with passing test
    - Commit 1: Still passing
    - Commit 2: BREAKS the test (this is the culprit)
    - Commit 3: Still broken
    - Commit 4: Still broken
    
    The test command is: bash test.sh
    """
    builder = GitRepoBuilder(tmp_path)
    builder.init()
    
    # Commit 0: Initial setup with passing test
    builder.write_file("test.sh", """#!/bin/bash
# Test script that checks if magic.txt contains "good"
if grep -q "good" magic.txt 2>/dev/null; then
    exit 0
else
    exit 1
fi
""")
    builder.write_file("magic.txt", "good")
    builder.commit("Initial commit with passing test")
    
    # Commit 1: Add some more content, still passing
    builder.write_file("README.md", "# Test Project")
    builder.commit("Add README")
    
    # Commit 2: BREAK the test - this is the culprit!
    builder.write_file("magic.txt", "bad")
    builder.commit("Change magic.txt to bad value")
    
    # Commit 3: Add unrelated changes (still broken)
    builder.write_file("other.txt", "some other content")
    builder.commit("Add unrelated file")
    
    # Commit 4: More unrelated changes (still broken)
    builder.write_file("README.md", "# Test Project\n\nUpdated readme.")
    builder.commit("Update README")
    
    return builder.build()


@pytest.fixture
def multi_file_test_repo(tmp_path: Path) -> GitTestRepo:
    """
    Create a more complex test repo where the bug is in source code.
    
    This simulates a Python project where a function is broken.
    """
    builder = GitRepoBuilder(tmp_path)
    builder.init()
    
    # Commit 0: Initial setup
    builder.write_file("calculator.py", '''
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b
''')
    builder.write_file("test_calculator.py", '''#!/usr/bin/env python3
from calculator import add, subtract, multiply

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0

def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(1, 1) == 0

def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(0, 5) == 0

if __name__ == "__main__":
    test_add()
    test_subtract()
    test_multiply()
    print("All tests passed!")
''')
    builder.commit("Initial calculator implementation")
    
    # Commit 1: Add divide function
    builder.write_file("calculator.py", '''
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
''')
    builder.commit("Add divide function")
    
    # Commit 2: BREAK multiply function - THE CULPRIT
    builder.write_file("calculator.py", '''
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a + b  # BUG: wrong operation!

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
''')
    builder.commit("Refactor multiply (introduces bug)")
    
    # Commit 3: Add power function (still broken)
    builder.write_file("calculator.py", '''
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a + b  # BUG: still here

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b

def power(a, b):
    return a ** b
''')
    builder.commit("Add power function")
    
    # Commit 4: Update tests (still broken)
    builder.write_file("test_calculator.py", '''#!/usr/bin/env python3
from calculator import add, subtract, multiply, divide, power

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0

def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(1, 1) == 0

def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(0, 5) == 0

def test_divide():
    assert divide(6, 2) == 3

def test_power():
    assert power(2, 3) == 8

if __name__ == "__main__":
    test_add()
    test_subtract()
    test_multiply()
    test_divide()
    test_power()
    print("All tests passed!")
''')
    builder.commit("Add tests for divide and power")
    
    return builder.build()

