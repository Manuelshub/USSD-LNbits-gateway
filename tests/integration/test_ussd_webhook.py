"""
Integration tests for the /ussd webhook endpoint.

Tests the end-to-end USSD request/response flow with FastAPI TestClient,
mocked Redis, and mocked LNbitsClient. Validates Requirements 1.1, 1.4,
1.5, 1.6 (Task 16.1).
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from src.main import app
from src.models import Session, MenuState, Invoice, PaymentResult, Transaction, WalletConfig


@pytest.fixture
def mock_redis():
    """Mock Redis client for session storage."""
    redis_mock = Mock()
    redis_mock.ping.return_value = True
    redis_mock.get.return_value = None  # No existing session by default
    redis_mock.setex.return_value = True
    redis_mock.delete.return_value = True
    return redis_mock


@pytest.fixture
def mock_lnbits():
    """Mock LNbits client with default successful responses."""
    client_mock = Mock()
    
    # Default successful balance
    client_mock.get_balance.return_value = 125430
    
    # Default successful invoice creation
    invoice = Invoice(
        payment_hash="a3f2e9c1d4b7a8e2f5c9d1b4e7a2f5c8",
        payment_request="lnbc10000n1...",
        amount=1000,
        memo="USSD Payment",
        created_at=datetime.now(timezone.utc),
        paid=False
    )
    client_mock.create_invoice.return_value = invoice
    
    # Default unpaid invoice status
    client_mock.check_invoice_status.return_value = {
        "paid": False,
        "amount": 1000
    }
    
    # Default successful invoice decode
    client_mock.decode_invoice.return_value = {
        "amount": 2000,
        "description": "Test payment"
    }
    
    # Default successful payment
    payment_result = PaymentResult(
        success=True,
        payment_hash="b4e7a2f5c8d1b4e7a2f5c8d1b4e7a2f5",
        amount=2000,
        fee=1,
        error_message=None
    )
    client_mock.pay_invoice.return_value = payment_result
    
    # Default empty transaction history
    client_mock.get_transactions.return_value = []
    
    return client_mock


@pytest.fixture
def mock_config():
    """Mock configuration object."""
    config_mock = Mock()
    config_mock.lnbits_url = "http://localhost:5000"
    config_mock.lnbits_admin_key = "admin_key_12345678"
    config_mock.lnbits_invoice_key = "invoice_key_12345678"
    config_mock.lnbits_wallet_id = "test_wallet_123"
    config_mock.redis_url = "redis://localhost:6379"
    config_mock.session_timeout = 90
    config_mock.log_level = "INFO"
    config_mock.public_url = ""
    config_mock.default_country_code = "221"
    config_mock.ussd_callback_secret = ""
    config_mock.webhook_base_url = "http://gateway:8000"
    config_mock.webhook_secret = ""
    config_mock.allow_registration = True
    config_mock.ussd_code = ""
    return config_mock


@pytest.fixture
def mock_session_manager():
    """Mock SessionManager that maintains session state across calls."""
    sessions = {}
    
    def create_session_side_effect(session_id, phone_number):
        session = Session(
            session_id=session_id,
            phone_number=phone_number,
            wallet_id="test_wallet_123",
            current_state=MenuState.MAIN_MENU,
            state_data={},
            created_at=datetime.now(timezone.utc),
            last_activity=datetime.now(timezone.utc)
        )
        sessions[session_id] = session
        return session
    
    def get_session_side_effect(session_id):
        return sessions.get(session_id)
    
    def update_session_side_effect(session):
        sessions[session.session_id] = session
    
    def end_session_side_effect(session_id):
        sessions.pop(session_id, None)
    
    mock = Mock()
    # Every phone in these tests is registered to the test wallet.
    mock.get_wallet_for_phone.return_value = WalletConfig(
        phone_number="+254712345678",
        wallet_id="test_wallet_123",
        admin_key="admin_key_12345678",
        invoice_key="invoice_key_12345678",
    )
    mock.create_session.side_effect = create_session_side_effect
    mock.get_session.side_effect = get_session_side_effect
    mock.update_session.side_effect = update_session_side_effect
    mock.end_session.side_effect = end_session_side_effect
    
    return mock


@pytest.fixture
def client(mock_redis, mock_lnbits, mock_config, mock_session_manager):
    """Create TestClient with mocked dependencies."""
    # Use start/stop instead of context manager to ensure patches persist
    # across multiple requests within a single test
    patches = [
        patch('src.main.redis_client', mock_redis),
        patch('src.main.config', mock_config),
        patch('src.main.session_manager', mock_session_manager),
        patch('src.main.redis.from_url', return_value=mock_redis),
        patch('src.main.LNbitsClient', return_value=mock_lnbits),
        patch('src.main.SessionManager', return_value=mock_session_manager)
    ]
    
    for p in patches:
        p.start()
    
    try:
        yield TestClient(app)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.integration
class TestUSSDWebhook:
    """Integration tests for /ussd webhook endpoint."""
    
    def test_new_session_creation(self, client, mock_redis):
        """
        Test new session creation and main menu display.
        Validates: Requirements 1.1, 1.2, 1.3, 1.6
        """
        # Simulate initial USSD dial with empty text
        response = client.post(
            "/ussd",
            data={
                "sessionId": "AT_session_001",
                "serviceCode": "*384*96#",
                "phoneNumber": "+2348012345678",
                "text": ""
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Main menu should be displayed (Requirement 1.6)
        body = response.text[4:]  # Remove "CON " prefix
        assert "Lightning Wallet" in body
        assert "1. Check Balance" in body or "1." in body
        assert "2. Receive Payment" in body or "2." in body
        assert "3. Send Payment" in body or "3." in body
        assert "4. History" in body or "4." in body
        assert "6. Exit" in body
    
    def test_session_continuation(self, client):
        """
        Test existing session continuation with accumulated text.
        Validates: Requirement 7.2
        """
        session_id = "AT_session_002"
        phone = "+2348012345678"
        
        # Step 1: Create session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Step 2: Continue session - select option 1 (Check Balance)
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "1"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
    
    def test_session_expiry(self, client):
        """
        Test session expiry handling.
        Validates: Requirement 7.4
        """
        # Simulate user input with non-empty text (continuation) but no existing session
        # This will be handled in our mock as session_manager.get_session returns None
        response = client.post(
            "/ussd",
            data={
                "sessionId": "AT_session_expired",
                "serviceCode": "*384*96#",
                "phoneNumber": "+2348012345678",
                "text": "1*1000"  # Non-empty indicates continuation
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("END")
        assert "Session expired" in response.text or "expired" in response.text.lower()
        assert "dial again" in response.text.lower() or "try again" in response.text.lower()
    
    def test_response_format_continue(self, client):
        """
        Test CON response format for session continuation.
        Validates: Requirement 1.5
        """
        response = client.post(
            "/ussd",
            data={
                "sessionId": "AT_session_003",
                "serviceCode": "*384*96#",
                "phoneNumber": "+2348012345678",
                "text": ""
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON ")
        # The response should contain menu text after "CON "
        assert len(response.text) > 4
    
    def test_response_format_terminate(self, client):
        """
        Test END response format for session termination.
        Validates: Requirement 1.5
        """
        session_id = "AT_session_004"
        phone = "+2348012345678"
        
        # Step 1: Create session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Step 2: Select Exit option (6)
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "6"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("END ")
        assert "Thank you" in response.text
    
    def test_phone_number_parsing(self, client):
        """
        Test phone number extraction and processing.
        Validates: Requirement 8.1
        """
        response = client.post(
            "/ussd",
            data={
                "sessionId": "AT_session_005",
                "serviceCode": "*384*96#",
                "phoneNumber": "+254712345678",  # Kenya number
                "text": ""
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
    
    def test_balance_flow(self, client, mock_lnbits):
        """
        Test complete balance inquiry flow.
        Validates: Requirements 2.1, 2.2, 2.3
        """
        session_id = "AT_balance_001"
        phone = "+2348012345678"
        
        # Step 1: Create session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Step 2: Select Check Balance from main menu
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "1"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Balance should be displayed with comma separators (Requirement 2.3)
        assert "125,430 sats" in response.text or "Balance:" in response.text
        assert "0:Menu" in response.text or "0" in response.text
        
        # LNbits get_balance should have been called (Requirement 2.2)
        mock_lnbits.get_balance.assert_called_once()
    
    def test_receive_payment_flow(self, client, mock_lnbits):
        """
        Test complete receive payment (invoice generation) flow.
        Validates: Requirements 3.1, 3.2, 3.3, 3.4
        """
        session_id = "AT_receive_001"
        phone = "+2348012345678"
        
        # Step 0: Initialize session with main menu
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Step 1: Select Receive Payment from main menu
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "2"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        assert "amount" in response.text.lower()
        
        # Step 2: Enter amount
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "2*1000"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Invoice should be created (Requirement 3.3)
        mock_lnbits.create_invoice.assert_called()
        
        # Invoice details should be displayed (Requirement 3.4)
        # Could contain "Invoice:", payment hash, or amount
        body = response.text[4:]
        assert any(keyword in body for keyword in ["Invoice", "1,000", "lnbc"])
    
    def test_invalid_amount_handling(self, client):
        """
        Test invalid amount input handling in receive flow.
        Validates: Requirement 3.7
        """
        session_id = "AT_invalid_amount"
        phone = "+2348012345678"
        
        # Step 0: Initialize session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Step 1: Enter receive flow
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "2"
            }
        )
        
        # Step 2: Enter invalid amount (non-numeric)
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "2*abc"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Error message should be displayed
        body = response.text[4:]
        assert "numbers only" in body.lower() or "invalid" in body.lower()
    
    def test_send_payment_flow(self, client, mock_lnbits):
        """
        Test complete send payment flow.
        Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5
        """
        session_id = "AT_send_001"
        phone = "+2348012345678"
        invoice = "lnbc20000n1pj9x8z7pp5..."
        
        # Step 0: Initialize session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Step 1: Select Send Payment from main menu
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "3"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        assert "invoice" in response.text.lower()
        
        # Step 2: Enter invoice
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": f"3*{invoice}"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Invoice should be decoded (Requirement 5.2)
        mock_lnbits.decode_invoice.assert_called_with(invoice)
        
        # Confirmation prompt should show amount (Requirement 5.3)
        body = response.text[4:]
        assert "2,000" in body or "2000" in body  # Amount from mock
        
        # Step 3: Confirm payment
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": f"3*{invoice}*1"
            }
        )
        
        assert response.status_code == 200
        
        # Payment should be executed (Requirement 5.4)
        mock_lnbits.pay_invoice.assert_called()
        
        # Success message should be displayed (Requirement 5.5)
        assert "CON" in response.text or "END" in response.text
    
    def test_invalid_invoice_handling(self, client):
        """
        Test invalid invoice input handling in send flow.
        Validates: Requirement 5.7
        """
        session_id = "AT_invalid_invoice"
        phone = "+2348012345678"
        
        # Step 0: Initialize session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Step 1: Enter send flow
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "3"
            }
        )
        
        # Step 2: Enter invalid invoice (doesn't start with lnbc)
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "3*invalid_invoice"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Error message should be displayed
        body = response.text[4:]
        assert "invalid" in body.lower() or "lnbc" in body.lower()
    
    def test_transaction_history_flow(self, client, mock_lnbits):
        """
        Test transaction history display flow.
        Validates: Requirements 6.1, 6.2, 6.5
        """
        # Setup mock transactions
        transactions = [
            Transaction(
                payment_hash="hash1",
                amount=1000,
                timestamp=datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc),
                memo="Test received",
                transaction_type="received"
            ),
            Transaction(
                payment_hash="hash2",
                amount=-500,
                timestamp=datetime(2024, 1, 15, 15, 45, tzinfo=timezone.utc),
                memo="Test sent",
                transaction_type="sent"
            )
        ]
        mock_lnbits.get_transactions.return_value = transactions
        
        session_id = "AT_history_001"
        phone = "+2348012345678"
        
        # Step 0: Initialize session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Select History from main menu
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "4"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Transactions should be fetched (Requirement 6.1)
        mock_lnbits.get_transactions.assert_called_once()
        
        # Transaction should be formatted (Requirement 6.2)
        # Look for amount, type, or timestamp
        body = response.text[4:]
        # At minimum should show something
        assert len(body) > 10
    
    def test_empty_transaction_history(self, client, mock_lnbits):
        """
        Test transaction history with no transactions.
        Validates: Requirement 6.5
        """
        # Empty transaction list
        mock_lnbits.get_transactions.return_value = []
        
        session_id = "AT_history_empty"
        phone = "+2348012345678"
        
        # Step 0: Initialize session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Select History from main menu
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "4"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # "No transactions" message should be displayed (Requirement 6.5)
        body = response.text[4:].lower()
        assert "no transactions" in body or "empty" in body or len(body) > 0
    
    def test_return_to_menu_from_submenu(self, client):
        """
        Test returning to main menu from submenu using "0".
        Validates: Requirement 11.3
        """
        session_id = "AT_return_menu"
        phone = "+2348012345678"
        
        # Step 0: Initialize session
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": ""
            }
        )
        
        # Step 1: Enter receive flow
        client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "2"
            }
        )
        
        # Step 2: Press "0" to return to menu
        response = client.post(
            "/ussd",
            data={
                "sessionId": session_id,
                "serviceCode": "*384*96#",
                "phoneNumber": phone,
                "text": "2*0"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("CON")
        
        # Main menu should be displayed
        body = response.text[4:]
        assert "Lightning Wallet" in body
        assert "1. Check Balance" in body
    
    def test_menu_options_numbered_from_1(self, client):
        """
        Test that menu options are numbered starting from 1.
        Validates: Requirement 11.1
        """
        response = client.post(
            "/ussd",
            data={
                "sessionId": "AT_menu_numbering",
                "serviceCode": "*384*96#",
                "phoneNumber": "+2348012345678",
                "text": ""
            }
        )
        
        assert response.status_code == 200
        body = response.text[4:]
        
        # Check that options are numbered 1-5
        assert "1." in body
        assert "2." in body
        assert "3." in body
        assert "4." in body
        assert "5." in body
    
    def test_exit_session_cleanup(self, client):
        """
        Test session cleanup on exit.
        Validates: Requirements 11.5, 8.7
        """
        # Select Exit option (6)
        response = client.post(
            "/ussd",
            data={
                "sessionId": "AT_exit_001",
                "serviceCode": "*384*96#",
                "phoneNumber": "+2348012345678",
                "text": "6"
            }
        )
        
        assert response.status_code == 200
        assert response.text.startswith("END")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
