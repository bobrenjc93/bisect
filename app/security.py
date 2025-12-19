"""Security utilities for input validation and sanitization."""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SHA_PATTERN = re.compile(r'^[a-fA-F0-9]{7,40}$')
REPO_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9._-]+$')
OWNER_PATTERN = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$')

MAX_OWNER_LENGTH = 39
MAX_REPO_LENGTH = 100
MAX_COMMAND_LENGTH = 4096

# Dangerous patterns blocked in test commands to prevent injection attacks
DANGEROUS_COMMAND_PATTERNS = [
    r';\s*rm\s+-rf',
    r'\$\([^)]+\)',
    r'`[^`]+`',
    r'\|\s*sh\s*$',
    r'\|\s*bash\s*$',
    r'\|\s*zsh\s*$',
    r'>\s*/etc/',
    r'>\s*/proc/',
    r'>\s*/sys/',
    r'>\s*/dev/',
    r'curl\s+[^|]+\|\s*sh',
    r'curl\s+[^|]+\|\s*bash',
    r'wget\s+[^|]+\|\s*sh',
    r'wget\s+[^|]+\|\s*bash',
    r'\\x[0-9a-fA-F]{2}',
    r'\\u[0-9a-fA-F]{4}',
    r'base64\s+-d',
    r'export\s+PATH\s*=',
    r'export\s+LD_PRELOAD',
    r'export\s+LD_LIBRARY_PATH',
    r'nc\s+-e',
    r'ncat\s+-e',
    r'/dev/tcp/',
    r'/dev/udp/',
    r'\bsudo\b',
    r'\bsu\s+-',
    r'\bchmod\s+[0-7]*[sS]',
    r'\bchown\s+root',
]

_COMPILED_DANGEROUS_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in DANGEROUS_COMMAND_PATTERNS
]


class ValidationError(Exception):
    """Raised when input validation fails."""
    pass


def validate_sha(sha: str, field_name: str = "SHA") -> str:
    """Validate and normalize a commit SHA."""
    if not sha:
        raise ValidationError(f"{field_name} is required")
    
    sha = sha.strip().lower()
    
    if not SHA_PATTERN.match(sha):
        raise ValidationError(
            f"{field_name} must be a valid git SHA (7-40 hex characters)"
        )
    
    return sha


def validate_repo_owner(owner: str) -> str:
    """Validate a GitHub repository owner name."""
    if not owner:
        raise ValidationError("Repository owner is required")
    
    owner = owner.strip()
    
    if len(owner) > MAX_OWNER_LENGTH:
        raise ValidationError(
            f"Repository owner must be at most {MAX_OWNER_LENGTH} characters"
        )
    
    if not OWNER_PATTERN.match(owner):
        raise ValidationError(
            "Repository owner must contain only alphanumeric characters and hyphens"
        )
    
    return owner


def validate_repo_name(name: str) -> str:
    """Validate a GitHub repository name."""
    if not name:
        raise ValidationError("Repository name is required")
    
    name = name.strip()
    
    if len(name) > MAX_REPO_LENGTH:
        raise ValidationError(
            f"Repository name must be at most {MAX_REPO_LENGTH} characters"
        )
    
    if not REPO_NAME_PATTERN.match(name):
        raise ValidationError(
            "Repository name must contain only alphanumeric characters, dots, hyphens, and underscores"
        )
    
    # Reject reserved names
    if name.lower() in ('.', '..', '.git'):
        raise ValidationError(f"Repository name '{name}' is reserved")
    
    return name


def validate_test_command(command: str) -> str:
    """Validate a test command for dangerous patterns."""
    if not command:
        raise ValidationError("Test command is required")
    
    command = command.strip()
    
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValidationError(
            f"Test command must be at most {MAX_COMMAND_LENGTH} characters"
        )
    
    for pattern in _COMPILED_DANGEROUS_PATTERNS:
        if pattern.search(command):
            logger.warning(
                f"Blocked dangerous command pattern: {pattern.pattern}"
            )
            raise ValidationError(
                "Test command contains disallowed patterns. "
                "Please use simple test commands without shell tricks."
            )
    
    return command


def validate_installation_id(installation_id: int) -> int:
    """Validate a GitHub App installation ID."""
    if not isinstance(installation_id, int) or installation_id <= 0:
        raise ValidationError("Installation ID must be a positive integer")
    
    return installation_id


def sanitize_log_message(message: str) -> str:
    """Sanitize a log message by redacting potential secrets."""
    # GitHub tokens
    message = re.sub(r'ghp_[a-zA-Z0-9]{36}', '[GITHUB_PAT]', message)
    message = re.sub(r'ghs_[a-zA-Z0-9]{36}', '[GITHUB_TOKEN]', message)
    message = re.sub(r'ghu_[a-zA-Z0-9]{36}', '[GITHUB_TOKEN]', message)
    message = re.sub(r'gho_[a-zA-Z0-9]{36}', '[GITHUB_TOKEN]', message)
    
    # Access tokens in URLs
    message = re.sub(
        r'(x-access-token:)[^@]+(@)',
        r'\1[REDACTED]\2',
        message,
        flags=re.IGNORECASE
    )
    
    # Generic patterns
    message = re.sub(
        r'(password[=:]\s*)[^\s,}]+',
        r'\1[REDACTED]',
        message,
        flags=re.IGNORECASE
    )
    message = re.sub(
        r'(secret[=:]\s*)[^\s,}]+',
        r'\1[REDACTED]',
        message,
        flags=re.IGNORECASE
    )
    message = re.sub(
        r'(token[=:]\s*)[^\s,}]+',
        r'\1[REDACTED]',
        message,
        flags=re.IGNORECASE
    )
    message = re.sub(
        r'(api[_-]?key[=:]\s*)[^\s,}]+',
        r'\1[REDACTED]',
        message,
        flags=re.IGNORECASE
    )
    
    return message


class SecureFormatter(logging.Formatter):
    """Logging formatter that redacts sensitive information."""
    
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return sanitize_log_message(message)


def configure_secure_logging(level: int = logging.INFO) -> None:
    """Configure logging with secure formatting."""
    handler = logging.StreamHandler()
    handler.setFormatter(SecureFormatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    
    root_logger = logging.getLogger()
    root_logger.handlers = []
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

