"""End-to-end tests for git bisect functionality."""

import pytest
from pathlib import Path

from app.bisect_core import BisectJob, BisectResult, run_bisect
from app.local_runner import LocalBisectRunner
from tests.conftest import GitTestRepo, GitRepoBuilder


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

