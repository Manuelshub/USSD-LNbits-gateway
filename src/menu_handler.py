"""USSD menu handler and state machine.

Input validation, screen formatting (always within 160 characters, in the
user's language) and the :class:`MenuHandler` state machine that drives
registration, balance, receive, send, history and settings flows.
"""

from src.i18n import tr
from src.models import InputType, Invoice, Transaction, ValidationResult

# Maximum amount (exclusive upper bound) accepted for an invoice, in satoshis.
# Requirement 3.2 / Property 4: a valid amount is a positive integer that is
# strictly less than 100,000,000 sats.
MAX_AMOUNT_SATS = 100_000_000

# Maximum length of any single USSD menu screen. Feature phones require menu
# text to fit within 160 characters (Requirement 11.6, Property 8).
MAX_MENU_CHARS = 160

# BOLT11 prefixes per network (lnbcrt, regtest, starts with lnbc).
_INVOICE_PREFIXES = ("lnbc", "lntb", "lnsb")


def validate_amount(input_text: str) -> ValidationResult:
    """Validate a satoshi amount entered by the user.

    A valid amount is a positive integer strictly less than
    ``MAX_AMOUNT_SATS`` (100,000,000 sats).

    Args:
        input_text: Raw user input string.

    Returns:
        ValidationResult with ``parsed_value`` set to the integer amount on
        success, or an ``error_message`` describing why validation failed.

    Validates: Requirements 3.2, 3.7
    """
    if input_text is None:
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message="Please enter numbers only",
        )

    stripped = input_text.strip()

    try:
        amount = int(stripped)
    except (ValueError, TypeError):
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message="Please enter numbers only",
        )

    if amount <= 0:
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message="Amount must be positive",
        )

    if amount >= MAX_AMOUNT_SATS:
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message="Amount too large (max: 100M sats)",
        )

    return ValidationResult(valid=True, parsed_value=amount, error_message=None)


def validate_menu_choice(input_text: str, max_option: int) -> ValidationResult:
    """Validate a numeric menu selection.

    A valid choice is an integer between 0 and ``max_option`` inclusive.
    Option "0" is reserved for returning to the main menu, so 0 is always
    an acceptable choice in submenus.

    Args:
        input_text: Raw user input string.
        max_option: Highest numbered option available (N). Choices 0..N are
            considered valid.

    Returns:
        ValidationResult with ``parsed_value`` set to the chosen integer on
        success, or an ``error_message`` on failure.

    Validates: Requirements 11.2
    """
    if input_text is None:
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message=f"Invalid choice. Select 1-{max_option} or 0 for menu.",
        )

    stripped = input_text.strip()

    try:
        choice = int(stripped)
    except (ValueError, TypeError):
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message=f"Invalid choice. Select 1-{max_option} or 0 for menu.",
        )

    if choice < 0 or choice > max_option:
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message=f"Invalid choice. Select 1-{max_option} or 0 for menu.",
        )

    return ValidationResult(valid=True, parsed_value=choice, error_message=None)


def validate_invoice_string(input_text: str) -> ValidationResult:
    """Validate a Lightning (BOLT11) invoice payment request string.

    A valid invoice is a non-empty string beginning with a BOLT11 human
    readable prefix (case-insensitive): ``lnbc`` (mainnet, which also covers
    regtest's ``lnbcrt``), ``lntb``/``lntbs`` (testnet/signet) or ``lnsb``
    (simnet). Surrounding whitespace is stripped and
    the normalized lowercase invoice is returned as ``parsed_value``.

    Args:
        input_text: Raw user input string.

    Returns:
        ValidationResult with ``parsed_value`` set to the normalized invoice
        string on success, or an ``error_message`` on failure.

    Validates: Requirements 5.7, 9.5
    """
    if input_text is None:
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message="Invalid invoice. Must start with 'ln'.",
        )

    stripped = input_text.strip()

    if not stripped:
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message="Invalid invoice. Must start with 'ln'.",
        )

    normalized = stripped.lower()

    if not normalized.startswith(_INVOICE_PREFIXES):
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message="Invalid invoice. Must start with 'ln'.",
        )

    # A bare prefix with no data section is not a usable invoice.
    if len(normalized) <= len("lnbcrt"):
        return ValidationResult(
            valid=False,
            parsed_value=None,
            error_message="Invalid invoice. Incomplete payment request.",
        )

    return ValidationResult(valid=True, parsed_value=normalized, error_message=None)


def validate_input(input_text: str, expected_type: InputType, **kwargs) -> ValidationResult:
    """Dispatch validation based on the expected input type.

    Args:
        input_text: Raw user input string.
        expected_type: The expected :class:`InputType`.
        **kwargs: Extra arguments forwarded to the specific validator. For
            ``InputType.MENU_CHOICE`` a ``max_option`` keyword is required.

    Returns:
        ValidationResult produced by the matching validator.

    Validates: Requirements 3.2, 3.7, 5.7, 11.2, 9.5
    """
    if expected_type == InputType.NUMERIC_AMOUNT:
        return validate_amount(input_text)
    if expected_type == InputType.MENU_CHOICE:
        max_option = kwargs.get("max_option")
        if max_option is None:
            raise ValueError("max_option is required for MENU_CHOICE validation")
        return validate_menu_choice(input_text, max_option)
    if expected_type == InputType.INVOICE_STRING:
        return validate_invoice_string(input_text)

    raise ValueError(f"Unsupported input type: {expected_type}")


def format_balance(balance_sats: int, lang: str = "en") -> str:
    """Format a satoshi balance with comma thousands separators.

    The formatted string embeds the exact numeric value (grouped with commas)
    so that stripping the separators recovers the original integer.

    Args:
        balance_sats: Wallet balance in satoshis (non-negative integer).

    Returns:
        A display string such as ``"Balance: 1,234,567 sats"``.

    Validates: Requirements 2.3
    """
    return tr(lang, "Balance: {amount} sats", amount=f"{balance_sats:,}")


def format_transaction(transaction: Transaction, lang: str = "en") -> str:
    """Format a single transaction for display.

    Includes the transaction type (received/sent), the amount in satoshis with
    comma separators, and a timestamp. A leading sign indicates direction:
    ``+`` for received funds and ``-`` for sent funds.

    Args:
        transaction: The :class:`Transaction` to format.

    Returns:
        A single-line display string, e.g.
        ``"received +1,000 sats 2024-01-15 14:30"``.

    Validates: Requirements 6.2
    """
    sign = "+" if transaction.transaction_type == "received" else "-"
    amount_abs = abs(transaction.amount)
    timestamp = transaction.timestamp.strftime("%Y-%m-%d %H:%M")
    kind = tr(lang, transaction.transaction_type)
    return f"{kind} {sign}{amount_abs:,} sats {timestamp}"


def format_invoice_display(invoice: Invoice) -> str:
    """Format a Lightning invoice for display within the USSD char limit.

    The payment request is typically far longer than a USSD screen allows, so
    it is truncated to keep the full display within
    :data:`MAX_MENU_CHARS` (160) characters while still showing the amount and
    enough of the payment request for the user to scan/copy.

    Args:
        invoice: The :class:`Invoice` to display.

    Returns:
        A display string guaranteed to be at most 160 characters long.

    Validates: Requirements 11.6
    """
    header = f"Invoice: {invoice.amount:,} sats\n"
    footer = "\n0:Menu"
    # Characters remaining for the payment request after the fixed parts.
    budget = MAX_MENU_CHARS - len(header) - len(footer)

    request = invoice.payment_request
    if budget <= 0:
        # Degenerate case: header/footer already fill the screen. Fall back to
        # a minimal representation that still respects the limit.
        return f"Invoice: {invoice.amount:,} sats"[:MAX_MENU_CHARS]

    if len(request) > budget:
        ellipsis = "..."
        if budget > len(ellipsis):
            request = request[: budget - len(ellipsis)] + ellipsis
        else:
            request = request[:budget]

    display = f"{header}{request}{footer}"
    return display[:MAX_MENU_CHARS]


def format_error_menu(error_message: str, lang: str = "en") -> str:
    """Format an error screen offering retry and return-to-menu options.

    The resulting text always fits within :data:`MAX_MENU_CHARS` (160)
    characters; a long error message is truncated so the retry/menu options
    remain visible.

    Args:
        error_message: Human-readable description of what went wrong.

    Returns:
        A display string with ``1:Retry`` and ``0:Menu`` options, at most 160
        characters long.

    Validates: Requirements 11.6
    """
    options = "\n" + tr(lang, "1:Retry\n0:Menu")
    budget = MAX_MENU_CHARS - len(options)

    message = tr(lang, (error_message or "An error occurred").strip())
    if len(message) > budget:
        ellipsis = "..."
        if budget > len(ellipsis):
            message = message[: budget - len(ellipsis)] + ellipsis
        else:
            message = message[:budget]

    display = f"{message}{options}"
    return display[:MAX_MENU_CHARS]

# ---------------------------------------------------------------------------
# MenuHandler state machine (Task 5.5)
# ---------------------------------------------------------------------------

from typing import List as _List, Optional as _Optional

from src.accounts import hash_pin, pin_matches, validate_new_pin
from src.i18n import LANGUAGES
from src.lnbits_client import LNbitsAPIError
from src.logging_config import get_logger, log_audit_payment, log_api_error
from src.models import MenuResponse, MenuState, PaymentResult, Session
from src.phone import normalize_phone, phone_to_username
from src.session_manager import SessionStoreError

logger = get_logger(__name__)


# Main menu options, numbered 1..N (Requirement 1.6, 11.1).
MAIN_MENU_OPTIONS = [
    (1, "Check Balance"),
    (2, "Receive Payment"),
    (3, "Send Payment"),
    (4, "History"),
    (5, "Settings"),
    (6, "Exit"),
]

# Mapping from a main-menu selection to the submenu state it enters.
_MAIN_MENU_ROUTES = {
    1: MenuState.CHECK_BALANCE,
    2: MenuState.RECEIVE_PAYMENT,
    3: MenuState.SEND_PAYMENT,
    4: MenuState.TRANSACTION_HISTORY,
    5: MenuState.SETTINGS_MENU,
    6: MenuState.EXIT,
}

# States of the first-dial registration flow, where "0" exits instead of
# returning to a main menu the user does not have yet.
_REGISTRATION_STATES = {
    MenuState.REGISTER_LANGUAGE,
    MenuState.REGISTER_PIN,
    MenuState.REGISTER_PIN_CONFIRM,
}

# Footer shown on submenus offering a return to the main menu (Requirement 11.3).
_RETURN_FOOTER = "0:Menu"

# Key used in Session.state_data to track the current pagination page index.
_PAGE_KEY = "page"


class MenuHandler:
    """USSD menu state machine.

    Drives navigation between :class:`MenuState` values, generates the menu
    text for each screen (always within :data:`MAX_MENU_CHARS`, in the
    session's language), honours the ``"0"`` return-to-main-menu convention in
    every submenu, and paginates content longer than one screen.

    Optional services switch on the full feature set:

    * ``accounts`` (:class:`~src.accounts.AccountService`): registration,
      PIN-confirmed payments, send-to-phone-number and settings.
    * ``notifier`` (:class:`~src.notifier.SMSNotifier`): invoices delivered by
      SMS and incoming-payment alerts.
    * ``lightning_domain``: shows the user's Lightning address on Receive.
    * ``invoice_webhook_url``: LNbits calls it when a USSD invoice is paid.
    """

    def __init__(self, session_manager=None, lnbits_client=None, accounts=None,
                 notifier=None, lightning_domain: str = "",
                 invoice_webhook_url: str = "", country_code: str = "221",
                 ussd_code: str = ""):
        """Create a MenuHandler.

        Args:
            session_manager: Optional :class:`~src.session_manager.SessionManager`.
            lnbits_client: :class:`~src.lnbits_client.LNbitsClient` scoped to
                the session's wallet.
            accounts: Optional :class:`~src.accounts.AccountService`.
            notifier: Optional :class:`~src.notifier.SMSNotifier`.
            lightning_domain: Domain of Lightning addresses (empty: none).
            invoice_webhook_url: Paid-invoice webhook URL (empty: none).
            country_code: Prepended to local numbers typed as recipients.
            ussd_code: Shortcode mentioned in SMS alerts (e.g. ``*384*96#``).
        """
        self.session_manager = session_manager
        self.lnbits_client = lnbits_client
        self.accounts = accounts
        self.notifier = notifier
        self.lightning_domain = lightning_domain
        self.invoice_webhook_url = invoice_webhook_url
        self.country_code = country_code
        self.ussd_code = ussd_code

    # -- public API ---------------------------------------------------------

    def process_input(self, session: Session, user_input: str) -> MenuResponse:
        """Process a single user input and advance the state machine.

        Args:
            session: Current session state. Its ``current_state`` and
                ``state_data`` are updated in place and returned on the
                resulting :class:`MenuResponse`.
            user_input: Raw user input text for this step (may be empty for a
                freshly created session).

        Returns:
            A :class:`MenuResponse` carrying the next screen's display text, a
            ``continue_session`` flag (``True`` to keep the session open,
            ``False`` to terminate), and the updated session.

        Validates: Requirements 1.4, 1.6, 11.1, 11.3, 11.4, 11.6, 11.7
        """
        text = (user_input or "").strip()
        state = session.current_state

        if state in _REGISTRATION_STATES:
            return self._handle_registration(session, text)

        # The "wallet created" screen: any key continues to the main menu.
        if state == MenuState.REGISTERED:
            return self._go_to_main_menu(session)

        # The "0" key always returns to the main menu from any submenu
        # (Requirement 11.3). It has no special meaning on the main menu
        # itself, where numbering starts at 1.
        if state != MenuState.MAIN_MENU and text == "0":
            return self._go_to_main_menu(session)

        if state == MenuState.MAIN_MENU:
            return self._handle_main_menu(session, text)

        # Balance flow: the session rests in CHECK_BALANCE only when the
        # previous LNbits call failed and an error screen (1:Retry / 0:Menu) is
        # showing. Any key other than "0" retries (Requirement 2.5).
        if state == MenuState.CHECK_BALANCE:
            return self._enter_check_balance(session)

        # History flow: same retry convention as the balance flow.
        if state == MenuState.TRANSACTION_HISTORY:
            return self._enter_transaction_history(session)

        if state == MenuState.RECEIVE_MENU:
            return self._handle_receive_menu(session, text)

        # Receive-payment flow (Requirements 3.1-3.8): amount entry, then an
        # invoice shown on INVOICE_DISPLAY (or delivered by SMS).
        if state in (MenuState.RECEIVE_PAYMENT, MenuState.AMOUNT_INPUT):
            return self._handle_amount_input(session, text)

        # Invoice display: "1" moves to the payment-status check.
        if state == MenuState.INVOICE_DISPLAY:
            return self._handle_invoice_display(session, text)

        if state == MenuState.CHECK_PAYMENT_STATUS:
            return self._enter_check_payment_status(session)

        if state == MenuState.SEND_MENU:
            return self._handle_send_menu(session, text)

        if state == MenuState.RECIPIENT_INPUT:
            return self._handle_recipient_input(session, text)

        if state == MenuState.SEND_AMOUNT_INPUT:
            return self._handle_send_amount_input(session, text)

        # Send-to-invoice flow (Requirements 5.1-5.8).
        if state in (MenuState.SEND_PAYMENT, MenuState.INVOICE_INPUT):
            return self._handle_invoice_input(session, text)

        # Confirmation without a PIN (deployments without accounts).
        if state == MenuState.CONFIRM_PAYMENT:
            return self._handle_confirm_payment(session, text)

        if state == MenuState.PIN_ENTRY:
            return self._handle_pin_entry(session, text)

        if state == MenuState.SETTINGS_MENU:
            return self._handle_settings_menu(session, text)

        if state == MenuState.LANGUAGE_SELECT:
            return self._handle_language_select(session, text)

        if state in (MenuState.CHANGE_PIN_OLD, MenuState.CHANGE_PIN_NEW,
                     MenuState.CHANGE_PIN_CONFIRM):
            return self._handle_change_pin(session, text)

        # Paginated display states: any input other than "0" advances to the
        # next page (or wraps back to the first page once the end is reached).
        if state in (MenuState.BALANCE_DISPLAY, MenuState.HISTORY_DISPLAY,
                     MenuState.PAYMENT_RESULT):
            return self._handle_paginated_display(session, text)

        return self._render_current_state(session)

    def format_menu_text(self, menu_state: MenuState, data: _Optional[dict] = None,
                         lang: str = "en") -> str:
        """Generate the menu text for a state within the 160 character limit.

        Args:
            menu_state: The :class:`MenuState` to render.
            data: Optional context data for rendering (e.g. a balance value, a
                list of transactions, or the current pagination ``page``).
            lang: Language code of the screen.

        Returns:
            Menu text guaranteed to be at most :data:`MAX_MENU_CHARS` (160)
            characters long.

        Validates: Requirements 1.6, 11.1, 11.3, 11.6, 11.7
        """
        data = data or {}
        page = int(data.get(_PAGE_KEY, 0))

        if menu_state == MenuState.MAIN_MENU:
            text = self._main_menu_text(lang)
        elif menu_state in (MenuState.CHECK_BALANCE, MenuState.BALANCE_DISPLAY):
            text = self._paginate(self._balance_text(data, lang), page, lang)
        elif menu_state in (MenuState.RECEIVE_PAYMENT, MenuState.AMOUNT_INPUT):
            text = self._amount_prompt_text(lang)
        elif menu_state == MenuState.RECEIVE_MENU:
            text = self._receive_menu_text(data, lang)
        elif menu_state == MenuState.INVOICE_DISPLAY:
            text = self._invoice_display_text(data, lang)
        elif menu_state == MenuState.CHECK_PAYMENT_STATUS:
            text = self._payment_status_text(data, lang)
        elif menu_state == MenuState.SEND_MENU:
            text = self._send_menu_text(lang)
        elif menu_state == MenuState.RECIPIENT_INPUT:
            text = self._with_return_footer(tr(lang, "Enter recipient phone number:"), lang)
        elif menu_state == MenuState.SEND_AMOUNT_INPUT:
            text = self._with_return_footer(tr(lang, "Amount to send in sats:"), lang)
        elif menu_state in (MenuState.SEND_PAYMENT, MenuState.INVOICE_INPUT):
            text = self._invoice_prompt_text(lang)
        elif menu_state == MenuState.CONFIRM_PAYMENT:
            text = self._confirm_payment_text(data, lang)
        elif menu_state == MenuState.PIN_ENTRY:
            text = self._pin_entry_text(data, lang)
        elif menu_state == MenuState.PAYMENT_RESULT:
            text = self._payment_result_text(data, lang)
        elif menu_state in (MenuState.TRANSACTION_HISTORY, MenuState.HISTORY_DISPLAY):
            text = self._paginate(self._history_text(data, lang), page, lang)
        elif menu_state == MenuState.SETTINGS_MENU:
            text = self._settings_text(lang)
        elif menu_state == MenuState.LANGUAGE_SELECT:
            text = self._with_return_footer(self._language_options(), lang)
        elif menu_state == MenuState.REGISTER_LANGUAGE:
            text = self._welcome_text()
        elif menu_state == MenuState.EXIT:
            text = tr(lang, "Thank you for using Lightning Wallet")
        else:
            text = self._with_return_footer(tr(lang, "Unavailable"), lang)

        # Final guard: never exceed the USSD screen limit (Property 8).
        return text[:MAX_MENU_CHARS]

    def lightning_address(self, phone_number: str) -> str:
        """Lightning address of ``phone_number`` ("" when not configured)."""
        if not self.lightning_domain:
            return ""
        return f"{phone_to_username(phone_number)}@{self.lightning_domain}"

    # -- registration -------------------------------------------------------

    def _handle_registration(self, session: Session, text: str) -> MenuResponse:
        """First dial from an unknown phone: language, PIN, PIN again, wallet.

        The PIN is hashed as soon as it is entered; only the hash is kept in
        the session between the two PIN screens.
        """
        lang = session.language
        state = session.current_state

        if text == "0":
            return self._response(session, tr(lang, "Goodbye"), continue_session=False)

        if state == MenuState.REGISTER_LANGUAGE:
            choice = validate_menu_choice(text, max_option=len(LANGUAGES))
            if text == "" or not choice.valid or choice.parsed_value == 0:
                return self._response(session, self._welcome_text(), continue_session=True)
            session.language = LANGUAGES[choice.parsed_value - 1][0]
            session.current_state = MenuState.REGISTER_PIN
            session.state_data = {}
            return self._response(
                session, tr(session.language, "Create a 4-digit PIN:"), continue_session=True
            )

        if state == MenuState.REGISTER_PIN:
            error = validate_new_pin(text)
            if error:
                body = f"{tr(lang, error)}\n{tr(lang, 'Create a 4-digit PIN:')}"
                return self._response(session, body, continue_session=True)
            salt, digest = hash_pin(text)
            session.current_state = MenuState.REGISTER_PIN_CONFIRM
            session.state_data = {"pin_salt": salt, "pin_hash": digest}
            return self._response(session, tr(lang, "Re-enter your PIN:"), continue_session=True)

        # REGISTER_PIN_CONFIRM
        salt = session.state_data.get("pin_salt", "")
        digest = session.state_data.get("pin_hash", "")
        if not pin_matches(text, salt, digest):
            session.current_state = MenuState.REGISTER_PIN
            session.state_data = {}
            body = f"{tr(lang, 'PINs do not match.')}\n{tr(lang, 'Create a 4-digit PIN:')}"
            return self._response(session, body, continue_session=True)

        try:
            wallet = self.accounts.register(session.phone_number, salt, digest, lang)
        except (LNbitsAPIError, SessionStoreError) as error:
            log_api_error(
                logger, service='lnbits', operation='register',
                error_message=str(error), phone_number=session.phone_number,
                session_id=session.session_id,
            )
            return self._response(
                session, tr(lang, "Could not create wallet. Try later."), continue_session=False
            )

        session.wallet_id = wallet.wallet_id
        session.current_state = MenuState.REGISTERED
        session.state_data = {}
        lines = [tr(lang, "Wallet created!")]
        address = self.lightning_address(session.phone_number)
        if address:
            lines += [tr(lang, "Your Lightning address:"), address]
        lines.append(tr(lang, "Any key: Menu"))
        return self._response(session, "\n".join(lines), continue_session=True)

    # -- main menu ----------------------------------------------------------

    def _handle_main_menu(self, session: Session, text: str) -> MenuResponse:
        """Handle a selection made from the main menu."""
        lang = session.language
        # An empty input means a freshly initiated session: show the main menu.
        if text == "":
            session.current_state = MenuState.MAIN_MENU
            session.state_data = {}
            return self._response(session, self._main_menu_text(lang), continue_session=True)

        result = validate_menu_choice(text, max_option=len(MAIN_MENU_OPTIONS))
        # "0" is not a main-menu option (numbering starts at 1); re-render the
        # menu on it and on out-of-range/non-numeric choices.
        if not result.valid or result.parsed_value == 0:
            return self._response(session, self._main_menu_text(lang), continue_session=True)

        next_state = _MAIN_MENU_ROUTES[result.parsed_value]
        session.state_data = {_PAGE_KEY: 0}

        if next_state == MenuState.EXIT:
            session.current_state = MenuState.EXIT
            session.state_data = {}
            return self._response(
                session, tr(lang, "Thank you for using Lightning Wallet"), continue_session=False
            )

        # Balance inquiry performs an LNbits call immediately on entry
        # (Requirements 2.1-2.4).
        if next_state == MenuState.CHECK_BALANCE:
            session.current_state = MenuState.CHECK_BALANCE
            return self._enter_check_balance(session)

        # Receive: show the Lightning address when there is one, otherwise go
        # straight to the amount prompt (Requirement 3.1).
        if next_state == MenuState.RECEIVE_PAYMENT:
            if self.lightning_domain:
                session.current_state = MenuState.RECEIVE_MENU
                return self._response(
                    session, self._receive_menu_text({"phone": session.phone_number}, lang),
                    continue_session=True,
                )
            session.current_state = MenuState.AMOUNT_INPUT
            return self._response(session, self._amount_prompt_text(lang), continue_session=True)

        # Send: choose phone number or invoice when accounts exist, otherwise
        # prompt for the bolt11 invoice to pay (Requirement 5.1).
        if next_state == MenuState.SEND_PAYMENT:
            if self.accounts is not None:
                session.current_state = MenuState.SEND_MENU
                return self._response(session, self._send_menu_text(lang), continue_session=True)
            session.current_state = MenuState.INVOICE_INPUT
            return self._response(session, self._invoice_prompt_text(lang), continue_session=True)

        # Transaction history performs an LNbits call immediately on entry
        # (Requirements 6.1-6.5).
        if next_state == MenuState.TRANSACTION_HISTORY:
            session.current_state = MenuState.TRANSACTION_HISTORY
            return self._enter_transaction_history(session)

        # Settings
        session.current_state = MenuState.SETTINGS_MENU
        return self._response(session, self._settings_text(lang), continue_session=True)

    def _go_to_main_menu(self, session: Session) -> MenuResponse:
        """Reset the session back to the main menu (the "0" convention)."""
        session.current_state = MenuState.MAIN_MENU
        session.state_data = {}
        return self._response(session, self._main_menu_text(session.language), continue_session=True)

    # -- balance inquiry ----------------------------------------------------

    def _enter_check_balance(self, session: Session) -> MenuResponse:
        """Fetch and display the wallet balance (Requirements 2.1-2.5).

        On :class:`~src.lnbits_client.LNbitsAPIError` the session stays in
        :class:`MenuState.CHECK_BALANCE` and an error screen offering
        ``1:Retry`` / ``0:Menu`` is shown (Requirement 2.5).
        """
        try:
            balance_sats = self.lnbits_client.get_balance(session.wallet_id)
        except LNbitsAPIError as error:
            log_api_error(
                logger,
                service='lnbits',
                operation='get_balance',
                error_code=error.error_code,
                error_message=error.message,
                phone_number=session.phone_number,
                session_id=session.session_id
            )
            session.current_state = MenuState.CHECK_BALANCE
            session.state_data = {_PAGE_KEY: 0}
            return self._error(session, error.message or "Service unavailable")

        session.current_state = MenuState.BALANCE_DISPLAY
        session.state_data = {_PAGE_KEY: 0, "balance_sats": int(balance_sats)}
        return self._render_current_state(session)

    # -- transaction history ------------------------------------------------

    def _enter_transaction_history(self, session: Session) -> MenuResponse:
        """Fetch and display the last 5 transactions (Requirements 6.1-6.5).

        On :class:`~src.lnbits_client.LNbitsAPIError` the session stays in
        :class:`MenuState.TRANSACTION_HISTORY` with a retry/menu error screen.
        """
        try:
            transactions = self.lnbits_client.get_transactions(session.wallet_id, limit=5)
        except LNbitsAPIError as error:
            log_api_error(
                logger,
                service='lnbits',
                operation='get_transactions',
                error_code=error.error_code,
                error_message=error.message,
                phone_number=session.phone_number,
                session_id=session.session_id
            )
            session.current_state = MenuState.TRANSACTION_HISTORY
            session.state_data = {_PAGE_KEY: 0}
            return self._error(session, error.message or "Service unavailable")

        session.current_state = MenuState.HISTORY_DISPLAY
        session.state_data = {_PAGE_KEY: 0, "transactions": list(transactions)}
        return self._render_current_state(session)

    # -- receive payment / invoice generation -------------------------------

    def _handle_receive_menu(self, session: Session, text: str) -> MenuResponse:
        """Receive screen with the Lightning address; "1" requests an amount."""
        if text == "1":
            session.current_state = MenuState.AMOUNT_INPUT
            session.state_data = {_PAGE_KEY: 0}
            return self._response(
                session, self._amount_prompt_text(session.language), continue_session=True
            )
        return self._response(
            session, self._receive_menu_text({"phone": session.phone_number}, session.language),
            continue_session=True,
        )

    def _handle_amount_input(self, session: Session, text: str) -> MenuResponse:
        """Validate the entered amount and generate an invoice (Req 3.1-3.8).

        With an SMS notifier the full invoice is sent to the user by SMS (it
        cannot fit on a USSD screen); the screen confirms the SMS. Without
        one, a truncated invoice is shown as before.
        """
        lang = session.language
        session.current_state = MenuState.AMOUNT_INPUT

        if text == "":
            session.state_data = {_PAGE_KEY: 0}
            return self._response(session, self._amount_prompt_text(lang), continue_session=True)

        result = validate_amount(text)
        if not result.valid:
            # Invalid amount: show the error and re-prompt (Requirement 3.7).
            body = f"{tr(lang, result.error_message or 'Invalid amount')}\n{self._amount_prompt_text(lang)}"
            session.state_data = {_PAGE_KEY: 0}
            return self._response(session, body, continue_session=True)

        amount = result.parsed_value
        kwargs = {"webhook": self.invoice_webhook_url} if self.invoice_webhook_url else {}

        try:
            invoice = self.lnbits_client.create_invoice(session.wallet_id, amount, **kwargs)
            log_audit_payment(
                logger,
                operation="invoice_created",
                phone_number=session.phone_number,
                amount=amount,
                payment_hash=invoice.payment_hash,
                success=True
            )
        except LNbitsAPIError as error:
            log_api_error(
                logger,
                service='lnbits',
                operation='create_invoice',
                error_code=error.error_code,
                error_message=error.message,
                phone_number=session.phone_number,
                session_id=session.session_id,
                amount=amount
            )
            # Stay in AMOUNT_INPUT so a non-"0" retry re-enters amount entry.
            session.state_data = {_PAGE_KEY: 0}
            return self._error(session, error.message or "Service unavailable")

        if self.accounts is not None and invoice.payment_hash:
            self.accounts.remember_invoice(invoice.payment_hash, session.phone_number)

        sent_by_sms = False
        if self.notifier is not None and self.notifier.enabled and invoice.payment_request:
            self.notifier.send(
                session.phone_number,
                tr(lang, "Lightning invoice for {amount} sats:\n{bolt11}",
                   amount=f"{amount:,}", bolt11=invoice.payment_request),
            )
            sent_by_sms = True

        # Store the invoice in session state (Requirement 3.5) and display it
        # with check-status / main-menu options (Requirements 3.4, 3.6).
        session.current_state = MenuState.INVOICE_DISPLAY
        session.state_data = {_PAGE_KEY: 0, "invoice": invoice, "sent_by_sms": sent_by_sms}
        return self._render_current_state(session)

    def _handle_invoice_display(self, session: Session, text: str) -> MenuResponse:
        """Any key other than "0" checks the payment status (Requirement 3.6)."""
        session.current_state = MenuState.CHECK_PAYMENT_STATUS
        session.state_data[_PAGE_KEY] = 0
        return self._enter_check_payment_status(session)

    def _enter_check_payment_status(self, session: Session) -> MenuResponse:
        """Query and display the invoice payment status (Requirements 4.1-4.5).

        On :class:`~src.lnbits_client.LNbitsAPIError` the session stays in
        CHECK_PAYMENT_STATUS with a retry/menu error screen (Requirement 9.6).
        """
        session.current_state = MenuState.CHECK_PAYMENT_STATUS

        invoice = session.state_data.get("invoice")
        payment_hash = getattr(invoice, "payment_hash", None) if invoice is not None else None

        if not payment_hash:
            # No invoice in session (e.g. expired state): show the placeholder.
            return self._render_current_state(session)

        try:
            status = self.lnbits_client.check_invoice_status(payment_hash)
        except LNbitsAPIError as error:
            log_api_error(
                logger,
                service='lnbits',
                operation='check_invoice_status',
                error_code=error.error_code,
                error_message=error.message,
                phone_number=session.phone_number,
                session_id=session.session_id,
                payment_hash=payment_hash[:8] + "..." if len(payment_hash) > 8 else payment_hash
            )
            session.state_data[_PAGE_KEY] = 0
            return self._error(session, error.message or "Service unavailable")

        session.state_data["paid"] = bool(status.get("paid", False))
        session.state_data["amount"] = status.get("amount")

        # Requirement 8.5: Audit log when an invoice is paid (incoming payment)
        if session.state_data["paid"]:
            log_audit_payment(
                logger,
                operation="invoice_paid",
                phone_number=session.phone_number,
                amount=session.state_data.get("amount") or 0,
                payment_hash=payment_hash,
                success=True
            )

        return self._render_current_state(session)

    # -- send payment -------------------------------------------------------

    def _handle_send_menu(self, session: Session, text: str) -> MenuResponse:
        """Send submenu: "1" pays a phone number, "2" pays an invoice."""
        session.state_data = {_PAGE_KEY: 0}
        if text == "1":
            session.current_state = MenuState.RECIPIENT_INPUT
        elif text == "2":
            session.current_state = MenuState.INVOICE_INPUT
        else:
            session.current_state = MenuState.SEND_MENU
        return self._render_current_state(session)

    def _handle_recipient_input(self, session: Session, text: str) -> MenuResponse:
        """Validate the recipient's phone number and check it has a wallet."""
        lang = session.language
        if text == "":
            return self._render_current_state(session)

        recipient = normalize_phone(text, self.country_code)
        problem = None
        if recipient is None:
            problem = "Invalid phone number."
        elif recipient == session.phone_number:
            problem = "You cannot send to yourself."
        elif self.accounts.get(recipient) is None:
            problem = "This number has no wallet yet."

        if problem:
            body = f"{tr(lang, problem)}\n{tr(lang, 'Enter recipient phone number:')}"
            return self._response(session, self._with_return_footer(body, lang), continue_session=True)

        session.current_state = MenuState.SEND_AMOUNT_INPUT
        session.state_data = {_PAGE_KEY: 0, "recipient": recipient}
        return self._render_current_state(session)

    def _handle_send_amount_input(self, session: Session, text: str) -> MenuResponse:
        """Validate the amount to send to a phone number, then ask for the PIN."""
        lang = session.language
        if text == "":
            return self._render_current_state(session)

        result = validate_amount(text)
        if not result.valid:
            body = f"{tr(lang, result.error_message)}\n{tr(lang, 'Amount to send in sats:')}"
            return self._response(session, self._with_return_footer(body, lang), continue_session=True)

        if self._exceeds_balance(session, result.parsed_value):
            body = f"{tr(lang, 'Insufficient balance')}\n{tr(lang, 'Amount to send in sats:')}"
            return self._response(session, self._with_return_footer(body, lang), continue_session=True)

        session.current_state = MenuState.PIN_ENTRY
        session.state_data = {
            _PAGE_KEY: 0,
            "kind": "phone",
            "recipient": session.state_data.get("recipient"),
            "amount": result.parsed_value,
        }
        return self._render_current_state(session)

    def _handle_invoice_input(self, session: Session, text: str) -> MenuResponse:
        """Validate and decode the entered invoice, then ask to confirm.

        Implements the INVOICE_INPUT step of the send flow (Requirements
        5.1-5.3, 5.7). With accounts, confirmation requires the user's PIN
        (PIN_ENTRY); otherwise a plain "1:Confirm" screen is shown.
        """
        lang = session.language
        session.current_state = MenuState.INVOICE_INPUT

        if text == "":
            session.state_data = {_PAGE_KEY: 0}
            return self._response(session, self._invoice_prompt_text(lang), continue_session=True)

        result = validate_invoice_string(text)
        if not result.valid:
            # Invalid invoice: show the error and re-prompt (Requirement 5.7).
            body = f"{tr(lang, result.error_message or 'Invalid invoice')}\n{self._invoice_prompt_text(lang)}"
            session.state_data = {_PAGE_KEY: 0}
            return self._response(session, body, continue_session=True)

        payment_request = result.parsed_value

        try:
            decoded = self.lnbits_client.decode_invoice(payment_request)
        except LNbitsAPIError as error:
            log_api_error(
                logger,
                service='lnbits',
                operation='decode_invoice',
                error_code=error.error_code,
                error_message=error.message,
                phone_number=session.phone_number,
                session_id=session.session_id
            )
            session.state_data = {_PAGE_KEY: 0}
            if error.error_code == "HTTP_ERROR":
                # LNbits answered but rejected the invoice: it is the input
                # that is wrong, not the service. Re-prompt.
                body = f"{tr(lang, 'Invalid invoice.')}\n{self._invoice_prompt_text(lang)}"
                return self._response(session, body, continue_session=True)
            return self._error(session, error.message or "Service unavailable")

        amount = decoded.get("amount")
        if amount and self._exceeds_balance(session, amount):
            session.state_data = {_PAGE_KEY: 0}
            body = f"{tr(lang, 'Insufficient balance')}\n{self._invoice_prompt_text(lang)}"
            return self._response(session, body, continue_session=True)

        session.current_state = (
            MenuState.PIN_ENTRY if self.accounts is not None else MenuState.CONFIRM_PAYMENT
        )
        session.state_data = {
            _PAGE_KEY: 0,
            "kind": "invoice",
            "payment_request": payment_request,
            "amount": decoded.get("amount"),
        }
        return self._render_current_state(session)

    def _handle_confirm_payment(self, session: Session, text: str) -> MenuResponse:
        """Pay the invoice once the user confirms (no-PIN deployments)."""
        return self._pay_invoice(session)

    def _handle_pin_entry(self, session: Session, text: str) -> MenuResponse:
        """Check the PIN, then execute the pending payment.

        Too many wrong PINs lock the phone for a while and end the session.
        """
        lang = session.language
        if text == "":
            return self._render_current_state(session)

        wallet = self.accounts.get(session.phone_number)
        if wallet is None or not wallet.pin_hash:
            session.current_state = MenuState.SETTINGS_MENU
            session.state_data = {}
            body = f"{tr(lang, 'Set a PIN in Settings first.')}\n{self._settings_text(lang)}"
            return self._response(session, body, continue_session=True)

        check = self.accounts.verify_pin(wallet, text)
        if check.locked:
            return self._response(
                session,
                tr(lang, "Too many wrong PINs. Try again in {minutes} min.",
                   minutes=self.accounts.lockout_minutes),
                continue_session=False,
            )
        if not check.ok:
            body = (f"{tr(lang, 'Wrong PIN. Tries left: {left}', left=check.attempts_left)}\n"
                    f"{self._pin_entry_text(session.state_data, lang)}")
            return self._response(session, body, continue_session=True)

        if session.state_data.get("kind") == "phone":
            return self._send_to_phone(session)
        return self._pay_invoice(session)

    def _pay_invoice(self, session: Session) -> MenuResponse:
        """Pay the bolt11 invoice stored in the session (Requirements 5.4-5.6).

        Every payment attempt is written to the audit log with the phone
        number (masked), amount, payment hash, and timestamp, and never any key
        material (Requirements 8.5, 8.6).
        """
        payment_request = session.state_data.get("payment_request")
        amount = session.state_data.get("amount")

        # Defensive: if the invoice is missing (e.g. expired state), restart the
        # send flow by re-prompting for an invoice.
        if not payment_request:
            session.current_state = MenuState.INVOICE_INPUT
            session.state_data = {_PAGE_KEY: 0}
            return self._render_current_state(session)

        try:
            payment_result = self.lnbits_client.pay_invoice(session.wallet_id, payment_request)
        except LNbitsAPIError as error:
            log_api_error(
                logger,
                service='lnbits',
                operation='pay_invoice',
                error_code=error.error_code,
                error_message=error.message,
                phone_number=session.phone_number,
                session_id=session.session_id,
                amount=amount
            )
            self._log_payment(session, amount, None, success=False)
            session.current_state = MenuState.INVOICE_INPUT
            session.state_data = {_PAGE_KEY: 0}
            return self._error(session, error.message or "Payment failed")

        self._log_payment(
            session,
            payment_result.amount if payment_result.amount is not None else amount,
            payment_result.payment_hash,
            success=payment_result.success,
        )

        if not payment_result.success:
            # Failed payment: show the reason and return to invoice entry
            # (Requirement 5.6).
            session.current_state = MenuState.INVOICE_INPUT
            session.state_data = {_PAGE_KEY: 0}
            return self._error(session, payment_result.error_message or "Payment failed")

        session.current_state = MenuState.PAYMENT_RESULT
        session.state_data = {_PAGE_KEY: 0, "payment_result": payment_result}
        return self._render_current_state(session)

    def _send_to_phone(self, session: Session) -> MenuResponse:
        """Move sats to another registered phone number's wallet.

        Creates an invoice on the recipient's wallet and pays it from the
        sender's; LNbits settles it internally, with no routing fee.
        """
        recipient_phone = session.state_data.get("recipient")
        amount = session.state_data.get("amount")
        recipient = self.accounts.get(recipient_phone) if recipient_phone else None
        if recipient is None or not amount:
            session.current_state = MenuState.RECIPIENT_INPUT
            session.state_data = {_PAGE_KEY: 0}
            return self._error(session, "This number has no wallet yet.")

        try:
            invoice = self.accounts.client_for(recipient).create_invoice(
                recipient.wallet_id, amount, memo="USSD transfer"
            )
            payment_result = self.lnbits_client.pay_invoice(
                session.wallet_id, invoice.payment_request
            )
        except LNbitsAPIError as error:
            log_api_error(
                logger,
                service='lnbits',
                operation='send_to_phone',
                error_code=error.error_code,
                error_message=error.message,
                phone_number=session.phone_number,
                session_id=session.session_id,
                amount=amount
            )
            payment_result = PaymentResult(success=False, error_message=error.message)

        self._log_payment(session, amount, payment_result.payment_hash,
                          success=payment_result.success)

        if not payment_result.success:
            # "1" on the Send menu is "phone number", so 1:Retry restarts it.
            session.current_state = MenuState.SEND_MENU
            session.state_data = {_PAGE_KEY: 0}
            return self._error(session, payment_result.error_message or "Payment failed")

        if payment_result.amount is None:
            payment_result.amount = amount
        self._notify_received(recipient, amount, sender=session.phone_number)

        session.current_state = MenuState.PAYMENT_RESULT
        session.state_data = {
            _PAGE_KEY: 0,
            "payment_result": payment_result,
            "recipient": recipient_phone,
        }
        return self._render_current_state(session)

    def _notify_received(self, recipient, amount: int, sender: str = "") -> None:
        """SMS the recipient of a transfer (best effort)."""
        if self.notifier is None:
            return
        lang = recipient.language
        if sender:
            message = tr(lang, "You received {amount} sats from {sender}.",
                         amount=f"{amount:,}", sender=sender)
        else:
            message = tr(lang, "You received {amount} sats.", amount=f"{amount:,}")
        if self.ussd_code:
            message += " " + tr(lang, "Dial {code} to use it.", code=self.ussd_code)
        self.notifier.send(recipient.phone_number, message)

    def _log_payment(self, session: Session, amount: _Optional[int],
                     payment_hash: _Optional[str], success: bool) -> None:
        """Write a payment attempt to the audit log (Requirements 8.5, 8.6).

        Records the phone number (masked by the log formatter), amount,
        (masked) payment hash and a UTC timestamp. Never logs the full payment
        request or any API key material.
        """
        log_audit_payment(
            logger,
            operation="payment_sent",
            phone_number=session.phone_number,
            amount=amount if amount is not None else 0,
            payment_hash=payment_hash or "",
            success=success
        )

    # -- settings -----------------------------------------------------------

    def _handle_settings_menu(self, session: Session, text: str) -> MenuResponse:
        """Settings: "1" language, "2" change PIN."""
        if self.accounts is not None and text == "1":
            session.current_state = MenuState.LANGUAGE_SELECT
        elif self.accounts is not None and text == "2":
            wallet = self.accounts.get(session.phone_number)
            # Wallets mapped by an operator may have no PIN yet: set one
            # directly instead of asking for the current one.
            has_pin = wallet is not None and bool(wallet.pin_hash)
            session.current_state = (
                MenuState.CHANGE_PIN_OLD if has_pin else MenuState.CHANGE_PIN_NEW
            )
            session.state_data = {}
            prompt = "Enter current PIN:" if has_pin else "Enter new 4-digit PIN:"
            return self._response(
                session, self._with_return_footer(tr(session.language, prompt), session.language),
                continue_session=True,
            )
        return self._render_current_state(session)

    def _handle_language_select(self, session: Session, text: str) -> MenuResponse:
        choice = validate_menu_choice(text, max_option=len(LANGUAGES))
        if not choice.valid or choice.parsed_value == 0:
            return self._render_current_state(session)

        language = LANGUAGES[choice.parsed_value - 1][0]
        wallet = self.accounts.get(session.phone_number)
        if wallet is not None:
            self.accounts.set_language(wallet, language)
        session.language = language
        session.current_state = MenuState.MAIN_MENU
        session.state_data = {}
        body = f"{tr(language, 'Language updated.')}\n{self._main_menu_text(language)}"
        return self._response(session, body, continue_session=True)

    def _handle_change_pin(self, session: Session, text: str) -> MenuResponse:
        """Current PIN (when one exists), new PIN, new PIN again."""
        lang = session.language
        state = session.current_state
        wallet = self.accounts.get(session.phone_number)
        if wallet is None:
            return self._go_to_main_menu(session)

        def prompt(*lines) -> MenuResponse:
            body = "\n".join(tr(lang, line) for line in lines)
            return self._response(session, self._with_return_footer(body, lang), continue_session=True)

        if state == MenuState.CHANGE_PIN_OLD:
            check = self.accounts.verify_pin(wallet, text)
            if check.locked:
                return self._response(
                    session,
                    tr(lang, "Too many wrong PINs. Try again in {minutes} min.",
                       minutes=self.accounts.lockout_minutes),
                    continue_session=False,
                )
            if not check.ok:
                wrong = tr(lang, "Wrong PIN. Tries left: {left}", left=check.attempts_left)
                return prompt(wrong, "Enter current PIN:")
            session.current_state = MenuState.CHANGE_PIN_NEW
            return prompt("Enter new 4-digit PIN:")

        if state == MenuState.CHANGE_PIN_NEW:
            error = validate_new_pin(text)
            if error:
                return prompt(error, "Enter new 4-digit PIN:")
            salt, digest = hash_pin(text)
            session.current_state = MenuState.CHANGE_PIN_CONFIRM
            session.state_data = {"pin_salt": salt, "pin_hash": digest}
            return prompt("Re-enter your PIN:")

        # CHANGE_PIN_CONFIRM
        salt = session.state_data.get("pin_salt", "")
        digest = session.state_data.get("pin_hash", "")
        if not pin_matches(text, salt, digest):
            session.current_state = MenuState.CHANGE_PIN_NEW
            session.state_data = {}
            return prompt("PINs do not match.", "Enter new 4-digit PIN:")

        self.accounts.set_pin(wallet, salt, digest)
        session.current_state = MenuState.MAIN_MENU
        session.state_data = {}
        body = f"{tr(lang, 'PIN changed.')}\n{self._main_menu_text(lang)}"
        return self._response(session, body, continue_session=True)

    # -- paginated displays -------------------------------------------------

    def _handle_paginated_display(self, session: Session, text: str) -> MenuResponse:
        """Advance pagination on a display state, wrapping back to page 0."""
        lang = session.language
        full_text = self._full_text_for_state(session.current_state, session.state_data, lang)
        total_pages = len(self._split_pages(full_text, lang))

        next_page = int(session.state_data.get(_PAGE_KEY, 0)) + 1
        if next_page >= total_pages:
            next_page = 0

        session.state_data[_PAGE_KEY] = next_page
        return self._render_current_state(session)

    def _render_current_state(self, session: Session) -> MenuResponse:
        """Re-render the current state without changing it."""
        state_data = dict(session.state_data)
        state_data.setdefault("phone", session.phone_number)
        return self._response(
            session,
            self.format_menu_text(session.current_state, state_data, session.language),
            continue_session=True,
        )

    # -- per-state text builders -------------------------------------------

    def _main_menu_text(self, lang: str = "en") -> str:
        """Build the main menu text (Requirements 1.6, 11.1)."""
        lines = [tr(lang, "Lightning Wallet")]
        for number, label in MAIN_MENU_OPTIONS:
            lines.append(f"{number}. {tr(lang, label)}")
        return "\n".join(lines)

    @staticmethod
    def _language_options() -> str:
        return "\n".join(f"{i}. {name}" for i, (_, name) in enumerate(LANGUAGES, start=1))

    def _welcome_text(self) -> str:
        """First screen for a new phone, before a language is chosen."""
        return (f"{tr('en', 'Welcome to Lightning Wallet')}\n"
                f"Choose language / Choisissez:\n{self._language_options()}\n0. Exit")

    def _balance_text(self, data: dict, lang: str = "en") -> str:
        """Text for the balance screen."""
        balance = data.get("balance_sats")
        if balance is None:
            body = tr(lang, "Fetching balance...")
        else:
            body = format_balance(int(balance), lang)
        return self._with_return_footer(body, lang)

    def _amount_prompt_text(self, lang: str = "en") -> str:
        """Prompt for the amount to receive (Requirement 3.1)."""
        return self._with_return_footer(tr(lang, "Enter amount in sats:\n(Example: 1000)"), lang)

    def _receive_menu_text(self, data: dict, lang: str = "en") -> str:
        """Receive screen showing the user's Lightning address."""
        lines = [
            tr(lang, "Your Lightning address:"),
            self.lightning_address(data.get("phone", "")),
            tr(lang, "Share it to get paid."),
            tr(lang, "1. Request an amount"),
        ]
        return self._with_return_footer("\n".join(lines), lang)

    def _send_menu_text(self, lang: str = "en") -> str:
        lines = [tr(lang, "Send to:"), tr(lang, "1. Phone number"), tr(lang, "2. Lightning invoice")]
        return self._with_return_footer("\n".join(lines), lang)

    def _settings_text(self, lang: str = "en") -> str:
        if self.accounts is None:
            return self._with_return_footer(tr(lang, "Unavailable"), lang)
        lines = [tr(lang, "Settings"), tr(lang, "1. Language"), tr(lang, "2. Change PIN")]
        return self._with_return_footer("\n".join(lines), lang)

    def _invoice_display_text(self, data: dict, lang: str = "en") -> str:
        """Text for the generated-invoice screen (Requirements 3.4, 3.6).

        When the invoice went out by SMS the screen says so. Otherwise it
        shows the amount, a truncated payment hash and as much of the payment
        request as fits, plus the "1:Status" and "0:Menu" options.
        """
        invoice = data.get("invoice")
        if invoice is None:
            return self._with_return_footer(tr(lang, "Generating invoice..."), lang)

        hash_short = self._truncate_hash(invoice.payment_hash)
        footer = "\n" + tr(lang, "1:Status 0:Menu")

        if data.get("sent_by_sms"):
            body = tr(lang, "Invoice for {amount} sats sent to you by SMS.", amount=f"{invoice.amount:,}")
            return f"{body}\nRef: {hash_short}{footer}"[:MAX_MENU_CHARS]

        header = f"{tr(lang, 'Invoice: {amount} sats', amount=f'{invoice.amount:,}')}\nHash: {hash_short}\n"
        budget = MAX_MENU_CHARS - len(header) - len(footer)
        request = invoice.payment_request or ""
        if budget <= 0:
            return tr(lang, "Invoice: {amount} sats", amount=f"{invoice.amount:,}")[:MAX_MENU_CHARS]

        if len(request) > budget:
            ellipsis = "..."
            if budget > len(ellipsis):
                request = request[: budget - len(ellipsis)] + ellipsis
            else:
                request = request[:budget]

        return f"{header}{request}{footer}"[:MAX_MENU_CHARS]

    @staticmethod
    def _truncate_hash(payment_hash: str) -> str:
        """Shorten a payment hash for display as ``abcd1234...wxyz`` (Req 3.4)."""
        if not payment_hash:
            return ""
        if len(payment_hash) <= 12:
            return payment_hash
        return f"{payment_hash[:8]}...{payment_hash[-4:]}"

    def _payment_status_text(self, data: dict, lang: str = "en") -> str:
        """Text for the invoice payment-status screen (Requirements 4.3, 4.4)."""
        paid = data.get("paid")
        if paid is True:
            amount = data.get("amount")
            if amount is not None:
                body = tr(lang, "Paid: {amount} sats", amount=f"{int(amount):,}")
            else:
                body = tr(lang, "Paid")
        elif paid is False:
            body = tr(lang, "Pending\n1:Check again")
        else:
            body = tr(lang, "Checking status...")
        return self._with_return_footer(body, lang)

    def _invoice_prompt_text(self, lang: str = "en") -> str:
        """Prompt for a bolt11 invoice to pay (Requirement 5.1)."""
        return self._with_return_footer(tr(lang, "Enter invoice to pay:"), lang)

    def _confirm_line(self, data: dict, lang: str) -> str:
        amount = data.get("amount")
        if amount is None:
            return tr(lang, "Confirm payment?")
        if data.get("recipient"):
            return tr(lang, "Send {amount} sats to {recipient}?",
                      amount=f"{int(amount):,}", recipient=data["recipient"])
        return tr(lang, "Pay {amount} sats?", amount=f"{int(amount):,}")

    def _confirm_payment_text(self, data: dict, lang: str = "en") -> str:
        """Confirmation screen for an outgoing payment (no PIN)."""
        body = f"{self._confirm_line(data, lang)}\n{tr(lang, '1:Confirm')}"
        return self._with_return_footer(body, lang)

    def _pin_entry_text(self, data: dict, lang: str = "en") -> str:
        """Confirmation screen for an outgoing payment, asking for the PIN."""
        body = f"{self._confirm_line(data, lang)}\n{tr(lang, 'Enter PIN to confirm:')}"
        return self._with_return_footer(body, lang)

    def _payment_result_text(self, data: dict, lang: str = "en") -> str:
        """Result screen for an outgoing payment."""
        result = data.get("payment_result")
        if result is None:
            body = tr(lang, "Processing payment...")
        elif getattr(result, "success", False):
            amount = getattr(result, "amount", None)
            if amount is None:
                body = tr(lang, "Payment sent")
            elif data.get("recipient"):
                body = tr(lang, "Sent {amount} sats to {recipient}",
                          amount=f"{int(amount):,}", recipient=data["recipient"])
            else:
                body = tr(lang, "Sent {amount} sats", amount=f"{int(amount):,}")
        else:
            body = tr(lang, getattr(result, "error_message", None) or "Payment failed")
        return self._with_return_footer(body, lang)

    def _history_text(self, data: dict, lang: str = "en") -> str:
        """Text for the transaction-history screen."""
        transactions = data.get("transactions")
        if transactions is None:
            body = tr(lang, "Loading history...")
        elif len(transactions) == 0:
            body = tr(lang, "No transactions yet")
        else:
            body = "\n".join(format_transaction(tx, lang) for tx in transactions)
        return self._with_return_footer(body, lang)

    # -- helpers ------------------------------------------------------------

    def _with_return_footer(self, body: str, lang: str = "en") -> str:
        """Append the ``0:Menu`` return option to a submenu body (Req 11.3)."""
        return f"{body}\n{tr(lang, _RETURN_FOOTER)}"

    def _exceeds_balance(self, session: Session, amount: int) -> bool:
        """True when ``amount`` is more than the wallet holds.

        Lets the user fix the amount before typing a PIN. If the balance
        can't be read, returns False: the payment itself still checks.
        """
        try:
            return amount > int(self.lnbits_client.get_balance(session.wallet_id))
        except (LNbitsAPIError, TypeError, ValueError):
            return False

    def _error(self, session: Session, message: str) -> MenuResponse:
        """Error screen with retry/menu options in the session's language."""
        return self._response(
            session, format_error_menu(message, session.language), continue_session=True
        )

    def _full_text_for_state(self, state: MenuState, data: dict, lang: str = "en") -> str:
        """Return the complete (un-paginated) text for a display state."""
        if state in (MenuState.BALANCE_DISPLAY, MenuState.CHECK_BALANCE):
            return self._balance_text(data, lang)
        if state in (MenuState.HISTORY_DISPLAY, MenuState.TRANSACTION_HISTORY):
            return self._history_text(data, lang)
        if state == MenuState.INVOICE_DISPLAY:
            return self._invoice_display_text(data, lang)
        if state == MenuState.PAYMENT_RESULT:
            return self._payment_result_text(data, lang)
        return self.format_menu_text(state, data, lang)

    def _paginate(self, full_text: str, page: int, lang: str = "en") -> str:
        """Return a single page of ``full_text`` within the char limit.

        When the content spans multiple screens a "More" hint and page counter
        are appended (Requirement 11.7, Property 9).
        """
        pages = self._split_pages(full_text, lang)
        if page < 0 or page >= len(pages):
            page = 0
        return pages[page]

    def _split_pages(self, full_text: str, lang: str = "en") -> _List[str]:
        """Split ``full_text`` into screens of at most 160 characters."""
        if len(full_text) <= MAX_MENU_CHARS:
            return [full_text]

        # Reserve space for a navigation hint on multi-page output.
        hint_template = "\n[{cur}/{total}] " + tr(lang, "More:any key")
        # Worst-case hint length (double-digit page counts are ample for USSD).
        reserve = len(hint_template.format(cur=99, total=99))
        chunk_size = MAX_MENU_CHARS - reserve
        if chunk_size <= 0:
            chunk_size = MAX_MENU_CHARS

        raw_chunks = [
            full_text[i : i + chunk_size]
            for i in range(0, len(full_text), chunk_size)
        ]
        total = len(raw_chunks)
        return [
            f"{chunk}{hint_template.format(cur=idx + 1, total=total)}"[:MAX_MENU_CHARS]
            for idx, chunk in enumerate(raw_chunks)
        ]

    def _response(self, session: Session, display_text: str,
                  continue_session: bool) -> MenuResponse:
        """Build a :class:`MenuResponse`, enforcing the char limit."""
        return MenuResponse(
            display_text=display_text[:MAX_MENU_CHARS],
            continue_session=continue_session,
            updated_session=session,
        )
