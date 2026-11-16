"""
Test that all menu text generation respects the 160-character USSD limit.

This test validates Requirement 11.6 and Property 8 from the design document:
all menu screens must fit within 160 characters to ensure compatibility with
feature phone USSD interfaces.
"""

import pytest
from unittest.mock import Mock
from datetime import datetime, timezone

from src.menu_handler import MenuHandler, format_balance, format_transaction, format_invoice_display, format_error_menu, MAX_MENU_CHARS
from src.models import Session, MenuState, Invoice, Transaction


class TestMenuCharacterLimit:
    """Tests asserting 160-character limit for all menu generation."""
    
    def test_max_menu_chars_constant(self):
        """Verify MAX_MENU_CHARS is correctly set to 160."""
        assert MAX_MENU_CHARS == 160, "MAX_MENU_CHARS must be 160 per USSD specification"
    
    def test_main_menu_within_limit(self):
        """Main menu text must fit within 160 characters."""
        mock_session_manager = Mock()
        mock_lnbits = Mock()
        handler = MenuHandler(mock_session_manager, mock_lnbits)
        
        session = Session(
            session_id="test",
            phone_number="+1234567890",
            wallet_id="wallet",
            current_state=MenuState.MAIN_MENU,
            state_data={},
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc)
        )
        
        menu_text = handler.format_menu_text(MenuState.MAIN_MENU)
        assert len(menu_text) <= MAX_MENU_CHARS, f"Main menu exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_balance_display_within_limit(self):
        """Balance display with various amounts stays within limit."""
        test_cases = [
            1,  # Minimum
            1000,  # Small amount
            125430,  # Typical amount
            99999999,  # Large amount (max is 100M)
        ]
        
        for balance in test_cases:
            formatted = format_balance(balance)
            # The balance display includes the balance and menu options
            # The actual menu text will wrap this, so check realistic scenario
            menu_text = f"Balance: {formatted}\n\n0:Menu"
            assert len(menu_text) <= MAX_MENU_CHARS, \
                f"Balance display for {balance} sats exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_invoice_display_within_limit(self):
        """Invoice display with long payment requests stays within limit."""
        # Realistic long Lightning invoice
        long_invoice = "lnbc10u1p3p" + "x" * 300  # Very long invoice
        
        invoice = Invoice(
            payment_hash="a3f2e9c1d4b7a8e2f5c9d1b4e7a2f5c8",
            payment_request=long_invoice,
            amount=1000,
            memo="Test payment",
            created_at=datetime.now(timezone.utc),
            paid=False
        )
        
        menu_text = format_invoice_display(invoice)
        assert len(menu_text) <= MAX_MENU_CHARS, \
            f"Invoice display exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_transaction_formatting_within_limit(self):
        """Transaction formatting produces reasonable length strings."""
        transactions = [
            Transaction(
                payment_hash="hash1",
                amount=1000,
                timestamp=datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc),
                memo="Received payment",
                transaction_type="received"
            ),
            Transaction(
                payment_hash="hash2",
                amount=-50000,
                timestamp=datetime(2024, 1, 15, 15, 45, tzinfo=timezone.utc),
                memo="Sent payment with a long memo that should be handled",
                transaction_type="sent"
            )
        ]
        
        for txn in transactions:
            formatted = format_transaction(txn)
            # Individual transaction lines should be reasonable
            assert len(formatted) <= 100, \
                f"Single transaction line too long: {len(formatted)} chars"
    
    def test_error_menu_within_limit(self):
        """Error menus with long messages stay within limit."""
        test_errors = [
            "Service temporarily unavailable",
            "Invalid amount. Please enter numbers only between 1 and 100,000,000 sats",
            "LNbits API connection timeout. The service may be temporarily slow or unavailable",
            "x" * 200,  # Very long error message
        ]
        
        for error_msg in test_errors:
            menu_text = format_error_menu(error_msg)
            assert len(menu_text) <= MAX_MENU_CHARS, \
                f"Error menu exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_amount_input_prompt_within_limit(self):
        """Amount input prompt stays within limit."""
        mock_session_manager = Mock()
        mock_lnbits = Mock()
        handler = MenuHandler(mock_session_manager, mock_lnbits)
        
        menu_text = handler.format_menu_text(MenuState.AMOUNT_INPUT)
        assert len(menu_text) <= MAX_MENU_CHARS, \
            f"Amount input prompt exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_invoice_input_prompt_within_limit(self):
        """Invoice input prompt stays within limit."""
        mock_session_manager = Mock()
        mock_lnbits = Mock()
        handler = MenuHandler(mock_session_manager, mock_lnbits)
        
        menu_text = handler.format_menu_text(MenuState.INVOICE_INPUT)
        assert len(menu_text) <= MAX_MENU_CHARS, \
            f"Invoice input prompt exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_check_payment_status_within_limit(self):
        """Payment status check menus stay within limit."""
        mock_session_manager = Mock()
        mock_lnbits = Mock()
        handler = MenuHandler(mock_session_manager, mock_lnbits)
        
        # Test both paid and unpaid scenarios
        test_data = [
            {"paid": False, "amount": 1000},
            {"paid": True, "amount": 50000},
        ]
        
        for data in test_data:
            menu_text = handler.format_menu_text(MenuState.CHECK_PAYMENT_STATUS, data)
            assert len(menu_text) <= MAX_MENU_CHARS, \
                f"Payment status menu exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_payment_confirmation_within_limit(self):
        """Payment confirmation prompts stay within limit."""
        mock_session_manager = Mock()
        mock_lnbits = Mock()
        handler = MenuHandler(mock_session_manager, mock_lnbits)
        
        # Test with decoded invoice data
        data = {
            "decoded_amount": 25000,
            "decoded_description": "Payment for goods and services"
        }
        
        menu_text = handler.format_menu_text(MenuState.CONFIRM_PAYMENT, data)
        assert len(menu_text) <= MAX_MENU_CHARS, \
            f"Payment confirmation exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_payment_result_within_limit(self):
        """Payment result messages stay within limit."""
        mock_session_manager = Mock()
        mock_lnbits = Mock()
        handler = MenuHandler(mock_session_manager, mock_lnbits)
        
        # Test success and failure scenarios
        test_data = [
            {"success": True, "amount": 5000},
            {"success": False, "error": "Insufficient balance"},
            {"success": False, "error": "Payment routing failed after multiple attempts"},
        ]
        
        for data in test_data:
            menu_text = handler.format_menu_text(MenuState.PAYMENT_RESULT, data)
            assert len(menu_text) <= MAX_MENU_CHARS, \
                f"Payment result exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_history_display_within_limit(self):
        """Transaction history display stays within limit (may paginate)."""
        mock_session_manager = Mock()
        mock_lnbits = Mock()
        handler = MenuHandler(mock_session_manager, mock_lnbits)
        
        # Create multiple transactions
        transactions = [
            Transaction(
                payment_hash=f"hash{i}",
                amount=1000 * (i + 1) * (-1 if i % 2 else 1),
                timestamp=datetime(2024, 1, 15 + i, 14, 30, tzinfo=timezone.utc),
                memo=f"Transaction {i}",
                transaction_type="received" if i % 2 == 0 else "sent"
            )
            for i in range(5)
        ]
        
        data = {"transactions": transactions, "page": 1}
        menu_text = handler.format_menu_text(MenuState.HISTORY_DISPLAY, data)
        assert len(menu_text) <= MAX_MENU_CHARS, \
            f"History display exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"
    
    def test_all_menu_states_within_limit(self):
        """Test that all menu states produce text within the limit."""
        mock_session_manager = Mock()
        mock_lnbits = Mock()
        mock_lnbits.get_balance.return_value = 125430
        
        handler = MenuHandler(mock_session_manager, mock_lnbits)
        
        # Test all main menu states
        states_to_test = [
            (MenuState.MAIN_MENU, None),
            (MenuState.BALANCE_DISPLAY, {"balance": 125430}),
            (MenuState.AMOUNT_INPUT, None),
            (MenuState.INVOICE_INPUT, None),
        ]
        
        for state, data in states_to_test:
            menu_text = handler.format_menu_text(state, data)
            assert len(menu_text) <= MAX_MENU_CHARS, \
                f"Menu state {state.value} exceeds {MAX_MENU_CHARS} chars: {len(menu_text)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
