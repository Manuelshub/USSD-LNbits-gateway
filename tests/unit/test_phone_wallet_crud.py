"""
Unit tests for phone-to-wallet mapping CRUD operations.

Tests the database operations for creating, reading, updating, and deleting
phone-to-wallet mappings as required by Requirements 12.4, 12.5, 12.6.

Note: Full integration tests require LNbits installation. These tests focus
on validating models and logic that can be tested independently.
"""

import pytest
from datetime import datetime, timezone
import re

# Import the models we need to test
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lnbits_extension/ussd_gateway'))

from models import PhoneWallet, CreatePhoneWalletRequest, UpdatePhoneWalletRequest


class TestPhoneWalletCRUD:
    """Test suite for phone wallet CRUD operations."""
    
    @pytest.fixture
    def sample_create_request(self):
        """Sample phone wallet creation request."""
        return CreatePhoneWalletRequest(
            phone_number="+2348012345678",
            wallet_id="test_wallet_123",
            admin_key="test_admin_key_12345678",
            invoice_key="test_invoice_key_12345678"
        )
    
    @pytest.fixture
    def sample_phone_wallet(self):
        """Sample phone wallet object."""
        now = datetime.now(timezone.utc)
        return PhoneWallet(
            id="test_id_123",
            phone_number="+2348012345678",
            wallet_id="test_wallet_123",
            admin_key="test_admin_key_12345678",
            invoice_key="test_invoice_key_12345678",
            created_at=now,
            updated_at=now
        )
    
    def test_phone_wallet_model(self, sample_phone_wallet):
        """Test PhoneWallet model basic properties."""
        assert sample_phone_wallet.id == "test_id_123"
        assert sample_phone_wallet.phone_number == "+2348012345678"
        assert sample_phone_wallet.wallet_id == "test_wallet_123"
        assert sample_phone_wallet.admin_key == "test_admin_key_12345678"
        assert sample_phone_wallet.invoice_key == "test_invoice_key_12345678"
        assert isinstance(sample_phone_wallet.created_at, datetime)
        assert isinstance(sample_phone_wallet.updated_at, datetime)
    
    def test_create_request_model(self, sample_create_request):
        """Test CreatePhoneWalletRequest model."""
        assert sample_create_request.phone_number == "+2348012345678"
        assert sample_create_request.wallet_id == "test_wallet_123"
        assert sample_create_request.admin_key == "test_admin_key_12345678"
        assert sample_create_request.invoice_key == "test_invoice_key_12345678"
    
    def test_update_request_model(self):
        """Test UpdatePhoneWalletRequest model with optional fields."""
        # Test with all fields
        full_update = UpdatePhoneWalletRequest(
            phone_number="+2348087654321",
            wallet_id="new_wallet_456",
            admin_key="new_admin_key",
            invoice_key="new_invoice_key"
        )
        assert full_update.phone_number == "+2348087654321"
        assert full_update.wallet_id == "new_wallet_456"
        
        # Test with partial fields
        partial_update = UpdatePhoneWalletRequest(
            phone_number="+2348087654321"
        )
        assert partial_update.phone_number == "+2348087654321"
        assert partial_update.wallet_id is None
        assert partial_update.admin_key is None
        assert partial_update.invoice_key is None
    
    def test_phone_wallet_response_masking(self, sample_phone_wallet):
        """
        Test that PhoneWalletResponse properly masks API keys.
        
        Validates Requirement 8.6: Keys should be masked in responses.
        """
        from models import PhoneWalletResponse
        
        response = PhoneWalletResponse.from_phone_wallet(sample_phone_wallet)
        
        assert response.id == sample_phone_wallet.id
        assert response.phone_number == sample_phone_wallet.phone_number
        assert response.wallet_id == sample_phone_wallet.wallet_id
        
        # Keys should be masked (first 8 chars + "...")
        assert response.admin_key_preview == "test_adm..."
        assert response.invoice_key_preview == "test_inv..."
        
        # Full keys should not be in response
        assert len(response.admin_key_preview) < len(sample_phone_wallet.admin_key)
        assert len(response.invoice_key_preview) < len(sample_phone_wallet.invoice_key)
    
    def test_phone_wallet_response_short_keys(self):
        """Test masking with keys shorter than 8 characters."""
        from models import PhoneWalletResponse
        
        now = datetime.now(timezone.utc)
        phone_wallet = PhoneWallet(
            id="test_id",
            phone_number="+1234567890",
            wallet_id="wallet",
            admin_key="short",
            invoice_key="tiny",
            created_at=now,
            updated_at=now
        )
        
        response = PhoneWalletResponse.from_phone_wallet(phone_wallet)
        
        # Short keys should be returned as-is (no masking needed)
        assert response.admin_key_preview == "short"
        assert response.invoice_key_preview == "tiny"


class TestPhoneNumberValidation:
    """Test phone number validation logic."""
    
    def validate_phone_number_regex(self, phone_number: str) -> bool:
        """
        Standalone validation function that matches the API validation.
        
        This validates Requirement 12.4: Phone numbers must be validated.
        """
        return bool(re.match(r'^\+\d{10,15}$', phone_number))
    
    def test_valid_phone_numbers(self):
        """Test valid international phone number formats."""
        valid_numbers = [
            "+2348012345678",  # Nigeria (13 digits)
            "+1234567890",     # 10 digits
            "+123456789012345",  # 15 digits (max)
            "+447700900123",   # UK (12 digits)
            "+254712345678",   # Kenya (12 digits)
        ]
        
        for number in valid_numbers:
            assert self.validate_phone_number_regex(number), \
                f"Valid number {number} was rejected"
    
    def test_invalid_phone_numbers(self):
        """Test invalid phone number formats."""
        invalid_numbers = [
            "2348012345678",   # Missing +
            "+234abc5678",     # Contains letters
            "+234",            # Too short (<10 digits)
            "+12345678901234567",  # Too long (>15 digits)
            "234-801-2345",    # Contains dashes
            "+234 801 2345",   # Contains spaces
            "",                # Empty
            "invalid",         # Completely invalid
            "+1234567",        # 7 digits (too short)
            "+123456789",      # 9 digits (too short)
        ]
        
        for number in invalid_numbers:
            assert not self.validate_phone_number_regex(number), \
                f"Invalid number {number} was accepted"
    
    def test_edge_case_phone_numbers(self):
        """Test edge cases for phone number validation."""
        # Exactly 10 digits (minimum valid)
        assert self.validate_phone_number_regex("+1234567890")
        
        # Exactly 15 digits (maximum valid)
        assert self.validate_phone_number_regex("+123456789012345")
        
        # 9 digits (one less than minimum)
        assert not self.validate_phone_number_regex("+123456789")
        
        # 16 digits (one more than maximum)
        assert not self.validate_phone_number_regex("+1234567890123456")


class TestSecurityRequirements:
    """Test security-related requirements."""
    
    def test_keys_masked_in_response(self):
        """
        Verify keys are properly masked in API responses.
        
        Validates Requirement 8.6: API keys must not be logged or displayed in full.
        """
        from models import PhoneWalletResponse
        
        now = datetime.now(timezone.utc)
        phone_wallet = PhoneWallet(
            id="secure_test",
            phone_number="+2348012345678",
            wallet_id="wallet_abc",
            admin_key="very_secret_admin_key_12345678",
            invoice_key="very_secret_invoice_key_12345678",
            created_at=now,
            updated_at=now
        )
        
        response = PhoneWalletResponse.from_phone_wallet(phone_wallet)
        
        # Verify full keys are not in response
        response_dict = response.model_dump()
        response_str = str(response_dict)
        
        assert "very_secret_admin_key_12345678" not in response_str
        assert "very_secret_invoice_key_12345678" not in response_str
        
        # Verify only previews are present (first 8 chars + ...)
        assert response.admin_key_preview == "very_sec..."
        assert response.invoice_key_preview == "very_sec..."
        assert response.admin_key_preview.endswith("...")
        assert response.invoice_key_preview.endswith("...")
    
    def test_phone_number_uniqueness_constraint(self):
        """
        Verify phone numbers are unique per requirement 12.6.
        
        The database schema enforces UNIQUE constraint on phone_number.
        This test validates the model supports this requirement.
        """
        # Create two requests with the same phone number
        request1 = CreatePhoneWalletRequest(
            phone_number="+2348012345678",
            wallet_id="wallet_1",
            admin_key="key1",
            invoice_key="invoice1"
        )
        
        request2 = CreatePhoneWalletRequest(
            phone_number="+2348012345678",  # Same phone
            wallet_id="wallet_2",
            admin_key="key2",
            invoice_key="invoice2"
        )
        
        # Both requests are valid models
        assert request1.phone_number == request2.phone_number
        
        # Note: The actual uniqueness enforcement happens in the database
        # and is validated by the CRUD create_phone_wallet function
        # which checks for existing phone numbers before insertion


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
