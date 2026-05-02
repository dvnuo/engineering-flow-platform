"""Tests for file parser validators."""

import pytest
import tempfile
from pathlib import Path
import sys

from tests._lightweight_file_parser_loader import load_file_parser_lightweight

_file_parser_module, _file_parser_cleanup = load_file_parser_lightweight()
_validators = sys.modules["src.utils.file_parser.validators"]

validate_file_size = _validators.validate_file_size
validate_content_type = _validators.validate_content_type
sanitize_filename = _validators.sanitize_filename
get_safe_extension = _validators.get_safe_extension
is_image_file = _validators.is_image_file
FILENAME_PATTERN = _validators.FILENAME_PATTERN

_file_parser_cleanup()
_file_parser_cleanup = None


def teardown_module(_module):
    pass


class TestValidateFileSize:
    """Test file size validation."""
    
    def test_valid_size(self):
        """Test valid file size passes."""
        assert validate_file_size(1024, 10) is True
        assert validate_file_size(5 * 1024 * 1024, 10) is True
    
    def test_oversized(self):
        """Test oversized file fails."""
        assert validate_file_size(11 * 1024 * 1024, 10) is False
        assert validate_file_size(100 * 1024 * 1024, 10) is False
    
    def test_zero_size(self):
        """Test zero size is valid."""
        assert validate_file_size(0, 10) is True


class TestValidateContentType:
    """Test content type validation."""
    
    def test_exact_match(self):
        """Test exact type match."""
        assert validate_content_type("image/jpeg", ["image/jpeg"]) is True
        assert validate_content_type("image/png", ["image/png"]) is True
    
    def test_wildcard_match(self):
        """Test wildcard match."""
        assert validate_content_type("image/png", ["image/*"]) is True
        assert validate_content_type("application/pdf", ["application/*"]) is True
    
    def test_no_match(self):
        """Test no match returns False."""
        assert validate_content_type("image/bmp", ["image/jpeg", "image/png"]) is False
        assert validate_content_type("application/exe", ["image/*", "application/pdf"]) is False
    
    def test_empty_allowed(self):
        """Test empty allowed list."""
        assert validate_content_type("image/jpeg", []) is False


class TestSanitizeFilename:
    """Test filename sanitization."""
    
    def test_valid_filename(self):
        """Test valid filename passes through."""
        assert sanitize_filename("document.pdf") == "document.pdf"
        assert sanitize_filename("my_file-123.txt") == "my_file-123.txt"
    
    def test_path_stripped(self):
        """Test path is stripped."""
        assert sanitize_filename("/path/to/file.txt") == "file.txt"
        assert sanitize_filename("dir/../file.txt") == "file.txt"
    
    def test_invalid_characters(self):
        """Test invalid characters are handled."""
        result = sanitize_filename("file<>:*.txt")
        # Should return a random name or safe version
        assert len(result) > 0
    
    def test_starts_with_digit(self):
        """Test filename starting with digit."""
        result = sanitize_filename("123file.txt")
        assert result == "123file.txt"
    
    def test_too_long(self):
        """Test very long filename."""
        long_name = "a" * 300 + ".txt"
        result = sanitize_filename(long_name)
        assert len(result) <= 200
    
    def test_control_characters(self):
        """Test control characters are stripped."""
        result = sanitize_filename("file\x00\x01.txt")
        assert "\x00" not in result
        assert "\x01" not in result


class TestGetSafeExtension:
    """Test safe extension extraction."""
    
    def test_valid_extensions(self):
        """Test valid extensions."""
        assert get_safe_extension("file.jpg") == ".jpg"
        assert get_safe_extension("file.png") == ".png"
        assert get_safe_extension("file.JPEG") == ".jpeg"
    
    def test_no_extension(self):
        """Test file without extension."""
        assert get_safe_extension("file") == ""
    
    def test_invalid_extension(self):
        """Test invalid extension."""
        assert get_safe_extension("file.exe") == ""  # exe not in allowlist
        assert get_safe_extension("file.") == ""
    
    def test_double_extension(self):
        """Test double extension."""
        # Should return empty since .gz is not in allowlist
        result = get_safe_extension("file.tar.gz")
        assert result == ""


class TestIsImageFile:
    """Test image file detection."""
    
    def test_image_extensions(self):
        """Test image extensions return True."""
        assert is_image_file("photo.jpg") is True
        assert is_image_file("photo.jpeg") is True
        assert is_image_file("photo.png") is True
        assert is_image_file("photo.gif") is True
        assert is_image_file("photo.webp") is True
        assert is_image_file("photo.bmp") is False  # bmp not in allowlist
    
    def test_non_image_extensions(self):
        """Test non-image extensions return False."""
        assert is_image_file("document.pdf") is False
        assert is_image_file("data.csv") is False
        assert is_image_file("archive.zip") is False
        assert is_image_file("file.txt") is False


class TestFilenamePattern:
    """Test filename pattern regex."""
    
    def test_valid_patterns(self):
        """Test valid filename patterns."""
        assert FILENAME_PATTERN.match("file.txt") is not None
        assert FILENAME_PATTERN.match("my-file.pdf") is not None
        assert FILENAME_PATTERN.match("123.456") is not None
    
    def test_invalid_patterns(self):
        """Test invalid filename patterns."""
        assert FILENAME_PATTERN.match(".hidden") is None  # starts with dot
        assert FILENAME_PATTERN.match("-start.txt") is None  # starts with hyphen
        assert FILENAME_PATTERN.match("file name.txt") is None  # space
