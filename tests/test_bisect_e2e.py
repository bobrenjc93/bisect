"""End-to-end tests for git bisect functionality.

These tests create local git repositories with controlled commit histories
and verify that the bisect logic correctly identifies culprit commits.

Run with: uv run pytest tests/test_bisect_e2e.py -v
"""

import pytest
from pathlib import Path

from app.bisect_core import BisectJob, BisectResult, run_bisect
from app.local_runner import LocalBisectRunner
from tests.conftest import GitTestRepo, GitRepoBuilder


class TestStandaloneBisectE2E:
    """
    Standalone e2e test demonstrating the full bisect workflow.
    
    This test creates everything from scratch without any external dependencies:
    1. Creates a fresh git repository
    2. Adds a test script that initially passes
    3. Adds several good commits
    4. Introduces a commit that breaks the test
    5. Adds more commits after the break
    6. Runs bisect and verifies it finds the exact breaking commit
    """

    def test_bisect_finds_breaking_commit(self, tmp_path: Path):
        """
        Complete e2e test: create repo, add commits, break test, bisect to find culprit.
        
        Timeline:
            Commit 0: Initial - test passes (checks for 'PASS' in status.txt)
            Commit 1: Good - add feature A
            Commit 2: Good - add feature B  
            Commit 3: BAD - breaks test by changing status.txt to 'FAIL'
            Commit 4: Bad - add feature C (still broken)
            Commit 5: Bad - add feature D (still broken)
        
        Expected: Bisect should identify Commit 3 as the first bad commit.
        """
        from tests.conftest import GitRepoBuilder
        
        builder = GitRepoBuilder(tmp_path)
        builder.init()
        
        # Commit 0: Initial setup with passing test
        # The test checks if status.txt contains "PASS"
        builder.write_file("test.sh", """#!/bin/bash
# Simple test: exit 0 if status.txt contains PASS, exit 1 otherwise
grep -q "PASS" status.txt
""")
        builder.write_file("status.txt", "PASS")
        builder.commit("Initial commit - test passes")
        
        # Commit 1: Add feature A (test still passes)
        builder.write_file("feature_a.py", "# Feature A implementation")
        builder.commit("Add feature A")
        
        # Commit 2: Add feature B (test still passes)
        builder.write_file("feature_b.py", "# Feature B implementation")
        builder.commit("Add feature B")
        
        # Commit 3: BREAKING CHANGE - this is the culprit!
        builder.write_file("status.txt", "FAIL")  # This breaks the test
        builder.commit("Update status - BREAKS TEST")
        
        # Commit 4: Add feature C (test is now broken)
        builder.write_file("feature_c.py", "# Feature C implementation")
        builder.commit("Add feature C")
        
        # Commit 5: Add feature D (test is still broken)
        builder.write_file("feature_d.py", "# Feature D implementation")
        builder.commit("Add feature D")
        
        repo = builder.build()
        
        # We have 6 commits (0-5)
        # Good: commits 0, 1, 2
        # Bad: commits 3, 4, 5
        good_sha = repo.get_commit_sha(2)  # Last known good
        bad_sha = repo.get_commit_sha(5)   # Current broken state
        expected_culprit = repo.get_commit_sha(3)  # The breaking commit
        
        # Run bisect
        result = run_bisect(
            repo_dir=str(repo.path),
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command="bash test.sh",
        )
        
        # Verify results
        assert result.success is True, f"Bisect failed: {result.error}"
        assert result.culprit_sha == expected_culprit, (
            f"Expected culprit {expected_culprit}, got {result.culprit_sha}"
        )
        assert result.culprit_message is not None
        assert "BREAKS TEST" in result.culprit_message
        print(f"\n✓ Bisect correctly found culprit commit: {result.culprit_sha[:8]}")
        print(f"  Message: {result.culprit_message}")

    def test_bisect_with_python_test_script(self, tmp_path: Path):
        """
        E2E test using a Python test script instead of bash.
        
        This simulates a more realistic scenario where the test command
        runs Python tests.
        """
        from tests.conftest import GitRepoBuilder
        
        builder = GitRepoBuilder(tmp_path)
        builder.init()
        
        # Commit 0: Initial setup with passing Python test
        builder.write_file("config.py", "ENABLED = True")
        builder.write_file("test_config.py", """#!/usr/bin/env python3
import sys
from config import ENABLED

def test_enabled():
    assert ENABLED is True, "ENABLED must be True"

if __name__ == "__main__":
    try:
        test_enabled()
        print("Test passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"Test failed: {e}")
        sys.exit(1)
""")
        builder.commit("Initial config with ENABLED=True")
        
        # Commit 1: Add utils (still passes)
        builder.write_file("utils.py", "# Utility functions")
        builder.commit("Add utils module")
        
        # Commit 2: BREAKING - disable the feature
        builder.write_file("config.py", "ENABLED = False")
        builder.commit("Disable feature - BREAKS TEST")
        
        # Commit 3: Add more code (still broken)
        builder.write_file("helpers.py", "# Helper functions")
        builder.commit("Add helpers module")
        
        repo = builder.build()
        
        # Good: commits 0, 1
        # Bad: commits 2, 3
        good_sha = repo.get_commit_sha(1)
        bad_sha = repo.get_commit_sha(3)
        expected_culprit = repo.get_commit_sha(2)
        
        result = run_bisect(
            repo_dir=str(repo.path),
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command="python3 test_config.py",
        )
        
        assert result.success is True, f"Bisect failed: {result.error}"
        assert result.culprit_sha == expected_culprit
        print(f"\n✓ Bisect correctly found Python test culprit: {result.culprit_sha[:8]}")
        print(f"  Message: {result.culprit_message}")


class TestBisectCore:
    """Tests for the core bisect logic."""

    def test_bisect_finds_culprit_commit(self, simple_test_repo: GitTestRepo):
        """
        Test that bisect correctly identifies the commit that broke the test.
        
        The simple_test_repo has:
        - Commit 0: good
        - Commit 1: good  
        - Commit 2: BAD (the culprit)
        - Commit 3: bad
        - Commit 4: bad
        """
        good_sha = simple_test_repo.get_commit_sha(1)  # Last known good
        bad_sha = simple_test_repo.get_commit_sha(4)   # Current broken state
        expected_culprit = simple_test_repo.get_commit_sha(2)  # The actual culprit
        
        result = run_bisect(
            repo_dir=str(simple_test_repo.path),
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command="bash test.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == expected_culprit
        assert result.culprit_message is not None
        assert "magic.txt" in result.culprit_message.lower() or "bad" in result.culprit_message.lower()
        assert result.error is None

    def test_bisect_with_first_commit_as_good(self, simple_test_repo: GitTestRepo):
        """Test bisect when the first commit is the good commit."""
        good_sha = simple_test_repo.first_commit
        bad_sha = simple_test_repo.last_commit
        expected_culprit = simple_test_repo.get_commit_sha(2)
        
        result = run_bisect(
            repo_dir=str(simple_test_repo.path),
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command="bash test.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == expected_culprit

    def test_bisect_adjacent_commits(self, simple_test_repo: GitTestRepo):
        """Test bisect with adjacent good/bad commits."""
        good_sha = simple_test_repo.get_commit_sha(1)
        bad_sha = simple_test_repo.get_commit_sha(2)
        expected_culprit = simple_test_repo.get_commit_sha(2)
        
        result = run_bisect(
            repo_dir=str(simple_test_repo.path),
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command="bash test.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == expected_culprit


class TestLocalBisectRunner:
    """Tests for the LocalBisectRunner."""

    def test_runner_on_existing_repo(self, simple_test_repo: GitTestRepo):
        """Test running bisect on an existing repository."""
        runner = LocalBisectRunner()
        
        good_sha = simple_test_repo.get_commit_sha(1)
        bad_sha = simple_test_repo.last_commit
        expected_culprit = simple_test_repo.get_commit_sha(2)
        
        result = runner.run_bisect_on_existing_repo(
            repo_path=simple_test_repo.path,
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command="bash test.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == expected_culprit

    def test_runner_clones_and_bisects(self, simple_test_repo: GitTestRepo, tmp_path: Path):
        """Test that LocalBisectRunner can clone a local repo and run bisect."""
        runner = LocalBisectRunner(work_dir=tmp_path / "work")
        
        # Use the test repo path as a "URL" (git clone works with local paths)
        job = BisectJob(
            repo_url=str(simple_test_repo.path),
            good_sha=simple_test_repo.get_commit_sha(1),
            bad_sha=simple_test_repo.last_commit,
            test_command="bash test.sh",
        )
        
        result = runner.run_bisect(job)
        
        assert result.success is True
        assert result.culprit_sha == simple_test_repo.get_commit_sha(2)


class TestBisectWithPythonTests:
    """Tests using Python test files to verify bisect works with real code."""

    def test_bisect_finds_bug_in_python_code(self, multi_file_test_repo: GitTestRepo):
        """
        Test that bisect correctly finds a bug introduced in Python code.
        
        The multi_file_test_repo has a calculator with a bug introduced in commit 2.
        """
        good_sha = multi_file_test_repo.get_commit_sha(1)
        bad_sha = multi_file_test_repo.last_commit
        expected_culprit = multi_file_test_repo.get_commit_sha(2)
        
        result = run_bisect(
            repo_dir=str(multi_file_test_repo.path),
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command="python3 test_calculator.py",
        )
        
        assert result.success is True
        assert result.culprit_sha == expected_culprit
        assert "multiply" in result.culprit_message.lower() or "refactor" in result.culprit_message.lower()


class TestBisectCustomScenarios:
    """Test bisect with custom repository scenarios."""

    def test_bisect_single_bad_commit(self, git_repo_builder: GitRepoBuilder):
        """Test when only one commit is bad (the last one)."""
        repo = (git_repo_builder
            .init()
            .write_file("check.sh", "#!/bin/bash\ntest -f good.txt")
            .write_file("good.txt", "exists")
            .commit("Initial good state")
            .write_file("other.txt", "content")
            .commit("Add other file")
            .delete_file("good.txt")  # This breaks the test
            .commit("Remove good.txt (breaks test)")
            .build())
        
        result = run_bisect(
            repo_dir=str(repo.path),
            good_sha=repo.get_commit_sha(1),
            bad_sha=repo.last_commit,
            test_command="bash check.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == repo.last_commit

    def test_bisect_first_commit_is_bad(self, git_repo_builder: GitRepoBuilder):
        """Test when the first commit after good is the bad one."""
        repo = (git_repo_builder
            .init()
            .write_file("test.sh", "#!/bin/bash\nexit 0")
            .commit("Good commit")
            .write_file("test.sh", "#!/bin/bash\nexit 1")
            .commit("Bad commit")
            .build())
        
        result = run_bisect(
            repo_dir=str(repo.path),
            good_sha=repo.first_commit,
            bad_sha=repo.last_commit,
            test_command="bash test.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == repo.last_commit

    def test_bisect_many_commits(self, git_repo_builder: GitRepoBuilder):
        """Test bisect with many commits to verify binary search works."""
        builder = git_repo_builder.init()
        
        # Create 10 good commits
        builder.write_file("counter.txt", "0")
        builder.write_file("test.sh", """#!/bin/bash
value=$(cat counter.txt)
if [ "$value" -lt 15 ]; then
    exit 0
else
    exit 1
fi
""")
        builder.commit("Initial setup")
        
        for i in range(1, 20):
            builder.write_file("counter.txt", str(i))
            builder.commit(f"Set counter to {i}")
        
        repo = builder.build()
        
        # Commits 0-14 are good (counter 0-14), commit 15+ are bad
        good_sha = repo.first_commit
        bad_sha = repo.last_commit
        expected_culprit = repo.get_commit_sha(15)  # When counter becomes 15
        
        result = run_bisect(
            repo_dir=str(repo.path),
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command="bash test.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == expected_culprit
        assert "15" in result.culprit_message

    def test_bisect_with_test_in_subdirectory(self, git_repo_builder: GitRepoBuilder):
        """Test bisect with test script in a subdirectory."""
        repo = (git_repo_builder
            .init()
            .write_file("src/config.json", '{"debug": false}')
            .write_file("tests/run_tests.sh", """#!/bin/bash
cd "$(dirname "$0")/.."
grep -q '"debug": false' src/config.json
""")
            .commit("Initial setup")
            .write_file("src/main.py", "print('hello')")
            .commit("Add main.py")
            .write_file("src/config.json", '{"debug": true}')  # Breaks test
            .commit("Enable debug mode (breaks test)")
            .write_file("src/util.py", "# utils")
            .commit("Add utils")
            .build())
        
        result = run_bisect(
            repo_dir=str(repo.path),
            good_sha=repo.get_commit_sha(1),
            bad_sha=repo.last_commit,
            test_command="bash tests/run_tests.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == repo.get_commit_sha(2)


class TestBisectEdgeCases:
    """Test edge cases and error handling."""

    def test_bisect_with_empty_test_output(self, git_repo_builder: GitRepoBuilder):
        """Test when test produces no output but has correct exit codes."""
        repo = (git_repo_builder
            .init()
            .write_file("silent_test.sh", "#!/bin/bash\nexit 0")
            .commit("Initial good")
            .write_file("silent_test.sh", "#!/bin/bash\nexit 1")
            .commit("Break it silently")
            .build())
        
        result = run_bisect(
            repo_dir=str(repo.path),
            good_sha=repo.first_commit,
            bad_sha=repo.last_commit,
            test_command="bash silent_test.sh",
        )
        
        assert result.success is True
        assert result.culprit_sha == repo.last_commit

    def test_bisect_result_includes_output(self, simple_test_repo: GitTestRepo):
        """Test that result includes bisect log output."""
        result = run_bisect(
            repo_dir=str(simple_test_repo.path),
            good_sha=simple_test_repo.first_commit,
            bad_sha=simple_test_repo.last_commit,
            test_command="bash test.sh",
        )
        
        assert result.success is True
        assert result.output is not None
        assert len(result.output) > 0

