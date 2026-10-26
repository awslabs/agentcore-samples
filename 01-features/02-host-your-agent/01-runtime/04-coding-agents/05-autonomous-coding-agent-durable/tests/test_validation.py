"""Tests for shared/validation.py — ticket ID and path validation."""
import os
import pytest

from shared.validation import (
    validate_ticket_id,
    validate_path_within_base,
    ValidationError,
    TICKET_ID_PATTERN,
    MAX_TICKET_ID_LENGTH,
)


class TestValidateTicketId:
    """Test ticket ID validation against the strict allowlist."""

    # --- Valid IDs ---
    @pytest.mark.parametrize("tid", [
        "TICKET-1",
        "TICKET-101",
        "my_ticket",
        "A",
        "a123",
        "HELLO-WORLD-123",
        "T" * 64,  # max length
        "A-b_C-d_E",
    ])
    def test_valid_ticket_ids(self, tid):
        assert validate_ticket_id(tid) == tid

    # --- Invalid IDs: traversal attempts ---
    @pytest.mark.parametrize("tid", [
        "../etc/passwd",
        "TICKET/../secret",
        "..%2f..%2fetc",
        "TICKET/sub",
        "TICKET\\sub",
        "TICKET\x00evil",
    ])
    def test_traversal_attempts_rejected(self, tid):
        with pytest.raises(ValidationError):
            validate_ticket_id(tid)

    # --- Invalid IDs: bad characters ---
    @pytest.mark.parametrize("tid", [
        "",
        " ",
        "ticket with spaces",
        "ticket\ttab",
        "ticket\nnewline",
        "ticket;rm -rf /",
        "ticket$(whoami)",
        "ticket`id`",
        "ticket|cat /etc/passwd",
        "ticket&& curl evil.com",
        "ticket.txt",  # dots not allowed
        ".hidden",
    ])
    def test_invalid_characters_rejected(self, tid):
        with pytest.raises(ValidationError):
            validate_ticket_id(tid)

    # --- Edge cases ---
    def test_too_long(self):
        with pytest.raises(ValidationError, match="too long"):
            validate_ticket_id("A" * 65)

    def test_none_rejected(self):
        with pytest.raises(ValidationError):
            validate_ticket_id(None)

    def test_integer_rejected(self):
        with pytest.raises(ValidationError, match="must be a string"):
            validate_ticket_id(123)

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationError, match="required"):
            validate_ticket_id("")

    def test_null_byte_rejected(self):
        with pytest.raises(ValidationError, match="null bytes"):
            validate_ticket_id("TICKET\x00-1")

    def test_starts_with_hyphen_rejected(self):
        """IDs must start with alphanumeric."""
        with pytest.raises(ValidationError):
            validate_ticket_id("-TICKET")

    def test_starts_with_underscore_rejected(self):
        """IDs must start with alphanumeric."""
        with pytest.raises(ValidationError):
            validate_ticket_id("_TICKET")


class TestValidatePathWithinBase:
    """Test path confinement validation."""

    def test_relative_path_within_base(self, tmp_base):
        sub = os.path.join(tmp_base, "subdir")
        os.makedirs(sub)
        result = validate_path_within_base("subdir", tmp_base)
        assert result == sub

    def test_nested_relative_path(self, tmp_base):
        nested = os.path.join(tmp_base, "a", "b", "c")
        os.makedirs(nested)
        result = validate_path_within_base("a/b/c", tmp_base)
        assert result == nested

    def test_absolute_path_within_base(self, tmp_base):
        sub = os.path.join(tmp_base, "file.txt")
        # realpath works even if file doesn't exist
        result = validate_path_within_base(sub, tmp_base)
        assert result == sub

    def test_base_itself_is_valid(self, tmp_base):
        result = validate_path_within_base(tmp_base, tmp_base)
        assert result == os.path.realpath(tmp_base)

    # --- Traversal attempts ---
    def test_dot_dot_traversal_rejected(self, tmp_base):
        with pytest.raises(ValidationError, match="escapes base"):
            validate_path_within_base("../etc/passwd", tmp_base)

    def test_deep_traversal_rejected(self, tmp_base):
        with pytest.raises(ValidationError, match="escapes base"):
            validate_path_within_base("a/../../etc/passwd", tmp_base)

    def test_absolute_path_outside_base_rejected(self, tmp_base):
        with pytest.raises(ValidationError, match="escapes base"):
            validate_path_within_base("/etc/passwd", tmp_base)

    def test_symlink_escape_rejected(self, tmp_base):
        """Symlinks that point outside the base should be caught."""
        link_path = os.path.join(tmp_base, "sneaky_link")
        os.symlink("/etc", link_path)
        with pytest.raises(ValidationError, match="escapes base"):
            validate_path_within_base("sneaky_link/passwd", tmp_base)

    def test_double_symlink_escape(self, tmp_base):
        """Chain of symlinks escaping the base."""
        inner_dir = os.path.join(tmp_base, "inner")
        os.makedirs(inner_dir)
        link1 = os.path.join(inner_dir, "link1")
        os.symlink("/tmp", link1)  # nosec B108 — security test verifying symlink escape is blocked
        with pytest.raises(ValidationError, match="escapes base"):
            validate_path_within_base("inner/link1/escape", tmp_base)

    # --- Edge cases ---
    def test_empty_path_rejected(self, tmp_base):
        with pytest.raises(ValidationError, match="path is required"):
            validate_path_within_base("", tmp_base)

    def test_empty_base_rejected(self):
        with pytest.raises(ValidationError, match="base directory is required"):
            validate_path_within_base("file.txt", "")

    def test_null_byte_in_path_rejected(self, tmp_base):
        with pytest.raises(ValidationError, match="null bytes"):
            validate_path_within_base("file\x00.txt", tmp_base)

    def test_null_byte_in_base_rejected(self, tmp_base):
        with pytest.raises(ValidationError, match="null bytes"):
            validate_path_within_base("file.txt", tmp_base + "\x00")

    def test_path_with_prefix_match_not_confused(self, tmp_path):
        """Ensure /base/dir doesn't match /base/directory (os.sep boundary)."""
        base = tmp_path / "app"
        base.mkdir()
        app_data = tmp_path / "app-data"
        app_data.mkdir()
        target = str(app_data / "secret.txt")
        with pytest.raises(ValidationError, match="escapes base"):
            validate_path_within_base(target, str(base))
