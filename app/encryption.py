"""Encryption utilities for sensitive data storage."""

import base64
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


class EncryptionError(Exception):
    """Raised when encryption or decryption fails."""
    pass


class FieldEncryptor:
    """Handles encryption and decryption of sensitive database fields using Fernet."""
    
    _instance: Optional["FieldEncryptor"] = None
    
    def __init__(self, key: Optional[str] = None):
        """Initialize with key from argument or ENCRYPTION_KEY env var."""
        if key is None:
            key = os.environ.get("ENCRYPTION_KEY")
        
        if not key:
            logger.warning(
                "ENCRYPTION_KEY not set. Encryption will be disabled. "
                "This is not recommended for production."
            )
            self._fernet = None
        else:
            try:
                self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
            except Exception as e:
                raise EncryptionError(f"Invalid encryption key: {e}")
    
    @classmethod
    def get_instance(cls) -> "FieldEncryptor":
        """Get the singleton encryptor instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None
    
    @property
    def is_enabled(self) -> bool:
        """Check if encryption is enabled."""
        return self._fernet is not None
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string."""
        if not plaintext:
            return plaintext
        
        if not self._fernet:
            logger.warning("Encryption disabled, storing plaintext")
            return plaintext
        
        try:
            encrypted = self._fernet.encrypt(plaintext.encode())
            return encrypted.decode()
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {e}")
    
    def decrypt(self, ciphertext: str) -> str:
        """Decrypt an encrypted string."""
        if not ciphertext:
            return ciphertext
        
        if not self._fernet:
            return ciphertext
        
        try:
            decrypted = self._fernet.decrypt(ciphertext.encode())
            return decrypted.decode()
        except InvalidToken:
            logger.warning("Failed to decrypt, returning as-is (may be plaintext)")
            return ciphertext
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {e}")


def derive_key_from_password(password: str, salt: Optional[bytes] = None) -> tuple[str, bytes]:
    """Derive a Fernet key from a password using PBKDF2."""
    if salt is None:
        salt = os.urandom(16)
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,  # OWASP recommended minimum
    )
    
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    return key.decode(), salt


def generate_key() -> str:
    """Generate a new Fernet encryption key."""
    return Fernet.generate_key().decode()


def encrypt_field(plaintext: str) -> str:
    """Encrypt a field value using the global encryptor."""
    return FieldEncryptor.get_instance().encrypt(plaintext)


def decrypt_field(ciphertext: str) -> str:
    """Decrypt a field value using the global encryptor."""
    return FieldEncryptor.get_instance().decrypt(ciphertext)

