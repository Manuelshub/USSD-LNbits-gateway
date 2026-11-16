"""FastAPI application entry point."""

import hmac
import os

import redis
from fastapi import FastAPI, Form, Query
from fastapi.responses import PlainTextResponse

from src.accounts import AccountService
from src.config import Config, ConfigManager, ConfigurationError
from src.demo import router as demo_router
from src.lnbits_client import LNbitsClient, LNbitsAPIError
from src.lnurl import lightning_domain, router as lnurl_router, webhook_url
from src.logging_config import get_logger, log_api_error, setup_logging
from src.menu_handler import MenuHandler
from src.models import MenuState
from src.notifier import SMSNotifier
from src.session_manager import SessionManager, SessionStoreError

# Structured logging will be configured in startup_event
logger = get_logger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="USSD-LNbits Gateway",
    description="Lightning wallet access via USSD for feature phones",
    version="0.1.0"
)

app.include_router(demo_router)
app.include_router(lnurl_router)

# Global application state (initialized on startup).
config: Config = None
redis_client: redis.Redis = None
session_manager: SessionManager = None
accounts: AccountService = None
notifier: SMSNotifier = None

# Registration screens; a phone without a wallet may only be in these.
_REGISTRATION_STATES = {
    MenuState.REGISTER_LANGUAGE,
    MenuState.REGISTER_PIN,
    MenuState.REGISTER_PIN_CONFIRM,
}


def _demo_mode() -> bool:
    """True when DEMO_MODE=true (browser demo on, callback secret optional)."""
    return os.getenv("DEMO_MODE", "false").lower() == "true"


@app.on_event("startup")
async def startup_event():
    """Load configuration and wire up dependencies on startup.
    
    Validates: Requirements 10.1, 10.5
    
    Performs startup validation by:
    1. Loading configuration from environment
    2. Initializing Redis connection and validating connectivity
    3. Validating LNbits API connectivity
    4. Failing fast if any service is unreachable
    """
    global config, redis_client, session_manager, accounts, notifier
    try:
        # Load and validate configuration (Requirement 10.1)
        config = ConfigManager.load_from_env()
        
        # Setup structured logging (Task 14.2, Requirements 8.5, 8.6)
        setup_logging(config.log_level)
        
        logger.info(
            "Configuration loaded successfully",
            extra={
                'event_type': 'startup',
                'lnbits_url': config.lnbits_url,
                'session_timeout': config.session_timeout,
            }
        )
        logger.info(ConfigManager.format_config(config, mask_secrets_flag=True))

    except ConfigurationError as e:
        # Configuration validation failed (Requirement 9.5, 10.1)
        logger.error(
            f"Configuration error: {e}",
            extra={'event_type': 'startup_error', 'error_type': 'configuration'}
        )
        raise RuntimeError(f"Configuration validation failed: {e}")
    
    try:
        # Redis-backed session store shared across all requests. The gateway is
        # stateless; all session state lives in Redis (Requirement 7.5).
        redis_client = redis.from_url(config.redis_url, decode_responses=True)
        session_manager = SessionManager(
            redis_client, timeout_seconds=config.session_timeout
        )
        accounts = AccountService(session_manager, lnbits_url=config.lnbits_url)
        notifier = SMSNotifier(
            at_username=config.at_username,
            at_api_key=config.at_api_key,
            sender_id=config.at_sender_id,
            redis_client=redis_client,
            demo_mode=_demo_mode(),
        )
        logger.info(
            "Session manager initialized",
            extra={'event_type': 'startup', 'session_timeout': config.session_timeout}
        )
        if not config.ussd_callback_secret:
            if not _demo_mode():
                # Without the secret anyone who can reach /ussd can claim any
                # phone number, so production refuses to start.
                logger.error(
                    "USSD_CALLBACK_SECRET is required when DEMO_MODE is off",
                    extra={'event_type': 'startup_error', 'error_type': 'configuration'}
                )
                raise RuntimeError(
                    "USSD_CALLBACK_SECRET is required when DEMO_MODE is off"
                )
            logger.warning(
                "USSD_CALLBACK_SECRET is not set: anyone who can reach /ussd can "
                "claim any phone number. Allowed only because DEMO_MODE is on.",
                extra={'event_type': 'security_warning'}
            )

        # Validate Redis connectivity (Requirement 10.5).
        # Fail fast if Redis is unreachable.
        try:
            redis_client.ping()
            logger.info(
                "✓ Redis connectivity validated",
                extra={'event_type': 'startup_validation', 'service': 'redis', 'status': 'ok'}
            )
        except Exception as e:
            log_api_error(logger, 'redis', 'ping', error_message=str(e))
            raise RuntimeError(f"Cannot connect to Redis: {e}")
        
        # Validate LNbits API connectivity (Requirement 10.5).
        # Fail fast if LNbits is unreachable or credentials are invalid.
        try:
            lnbits_client = LNbitsClient(
                base_url=config.lnbits_url,
                admin_key=config.lnbits_admin_key,
                invoice_key=config.lnbits_invoice_key,
            )
            # Perform a lightweight API call to verify connectivity.
            lnbits_client.get_balance(config.lnbits_wallet_id)
            logger.info(
                "✓ LNbits API connectivity validated",
                extra={'event_type': 'startup_validation', 'service': 'lnbits', 'status': 'ok'}
            )
        except LNbitsAPIError as e:
            # LNbits-specific error (timeout, auth failure, etc.)
            log_api_error(
                logger,
                'lnbits',
                'get_balance',
                error_code=e.error_code,
                error_message=e.message
            )
            raise RuntimeError(f"Cannot connect to LNbits: {e.message}")
        except Exception as e:
            log_api_error(logger, 'lnbits', 'connectivity_check', error_message=str(e))
            raise RuntimeError(f"Cannot connect to LNbits: {e}")
        
        logger.info(
            "🚀 Gateway startup complete - ready to receive USSD requests",
            extra={'event_type': 'startup_complete', 'status': 'ready'}
        )
        
    except RuntimeError:
        # Re-raise RuntimeError (already logged)
        raise
    except Exception as e:
        logger.error(
            f"Failed to start gateway: {e}",
            extra={'event_type': 'startup_error'},
            exc_info=True
        )
        raise RuntimeError(f"Gateway startup failed: {e}")


def _latest_input(text: str) -> str:
    """Extract the latest user input from Africa's Talking ``text``.

    Africa's Talking sends the full accumulated input for the session, with
    each step joined by ``*`` (e.g. ``"1*1000"``). The latest step the user
    just entered is the final segment.
    """
    if not text:
        return ""
    return text.split("*")[-1].strip()


@app.get("/health")
async def health_check():
    """Health check endpoint.
    
    Validates: Requirements 10.6
    
    Returns:
        200 OK with status object if both Redis and LNbits are operational,
        503 Service Unavailable if either service is unreachable.
    """
    health_status = {
        "status": "healthy",
        "version": "0.1.0",
        "redis": "unknown",
        "lnbits": "unknown"
    }
    
    # Check Redis connection (Requirement 9.3)
    try:
        redis_client.ping()
        health_status["redis"] = "connected"
        logger.debug(
            "Health check: Redis connection OK",
            extra={'event_type': 'health_check', 'service': 'redis', 'status': 'ok'}
        )
    except redis.RedisError as e:
        health_status["redis"] = "disconnected"
        health_status["status"] = "unhealthy"
        log_api_error(logger, 'redis', 'health_check', error_message=str(e))
    except Exception as e:
        health_status["redis"] = "error"
        health_status["status"] = "unhealthy"
        log_api_error(logger, 'redis', 'health_check', error_message=str(e))
    
    # Check LNbits API connection (Requirement 9.1, 9.2)
    try:
        # Use a lightweight endpoint to verify connectivity.
        # We create a test client with the global wallet credentials.
        test_client = LNbitsClient(
            base_url=config.lnbits_url,
            admin_key=config.lnbits_admin_key,
            invoice_key=config.lnbits_invoice_key,
        )
        # get_balance makes a simple GET request to /api/v1/wallet
        test_client.get_balance(config.lnbits_wallet_id)
        health_status["lnbits"] = "connected"
        logger.debug(
            "Health check: LNbits connection OK",
            extra={'event_type': 'health_check', 'service': 'lnbits', 'status': 'ok'}
        )
    except LNbitsAPIError as e:
        health_status["lnbits"] = f"disconnected ({e.error_code})"
        health_status["status"] = "unhealthy"
        log_api_error(logger, 'lnbits', 'health_check', error_code=e.error_code, error_message=e.message)
    except Exception as e:
        health_status["lnbits"] = "error"
        health_status["status"] = "unhealthy"
        log_api_error(logger, 'lnbits', 'health_check', error_message=str(e))
    
    # Return 503 if either service is unhealthy
    if health_status["status"] == "unhealthy":
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content=health_status,
            status_code=503
        )
    
    return health_status


@app.post("/ussd", response_class=PlainTextResponse)
async def ussd_callback(
    sessionId: str = Form(...),
    serviceCode: str = Form(...),
    phoneNumber: str = Form(...),
    text: str = Form(default=""),
    key: str = Query(default=""),
):
    """
    USSD webhook callback from Africa's Talking.

    Args:
        sessionId: Unique session identifier
        serviceCode: USSD short code dialed
        phoneNumber: User's phone number in international format
        text: Accumulated user input history (empty for a new session),
            with each step joined by "*"
        key: Shared secret from the callback URL (``/ussd?key=...``); required
            when USSD_CALLBACK_SECRET is set

    Returns:
        Plain text response: "CON ..." to continue or "END ..." to terminate

    Validates: Requirements 1.1, 1.2, 1.3, 1.5, 7.2, 8.1, 8.4
    """
    # Africa's Talking does not sign callbacks, so without this secret anyone
    # could POST here with someone else's phone number.
    secret = config.ussd_callback_secret if config is not None else ""
    if secret and not hmac.compare_digest(key, secret):
        logger.warning(
            "USSD callback with a wrong or missing key",
            extra={'event_type': 'auth_failure', 'session_id': sessionId}
        )
        return PlainTextResponse("END Access denied.", status_code=403)

    return handle_ussd(sessionId, serviceCode, phoneNumber, text)


def handle_ussd(session_id: str, service_code: str, phone_number: str, text: str) -> str:
    """Advance one USSD step and return "CON ..." or "END ..." text.

    Phones without a wallet go through registration (language, PIN) when
    ALLOW_REGISTRATION is on; registered phones get a menu bound to their own
    wallet. There is no shared fallback wallet.
    """
    # The raw text is never logged: it contains everything the user typed in
    # this session, PINs included.
    logger.info(
        "USSD request",
        extra={
            'event_type': 'ussd_request',
            'session_id': session_id,
            'phone_number': phone_number,
            'service_code': service_code,
            'input_length': len(text)
        }
    )

    user_input = _latest_input(text)

    try:
        # Retrieve the existing session for this session id (Requirement 7.2),
        # or create a new one for a freshly initiated session (Requirements
        # 1.1-1.3). A new session is recognised by an empty accumulated text.
        session = session_manager.get_session(session_id)

        if session is None:
            if text != "":
                # Accumulated input but no session: it expired mid-flow
                # (Requirement 7.4). Terminate so the user can redial.
                logger.info(
                    "Session expired; terminating",
                    extra={
                        'event_type': 'session_expired',
                        'session_id': session_id,
                        'phone_number': phone_number
                    }
                )
                return "END Session expired. Please dial again."
            session = session_manager.create_session(session_id, phone_number)

        # Requirements 8.1, 8.4: the phone number selects the wallet.
        wallet_config = session_manager.get_wallet_for_phone(phone_number)
        lnbits_client = None
        if wallet_config is None:
            if session.current_state not in _REGISTRATION_STATES:
                if not config.allow_registration:
                    session_manager.end_session(session.session_id)
                    return "END Registration is closed."
                session.current_state = MenuState.REGISTER_LANGUAGE
                session.state_data = {}
                user_input = ""
        else:
            session.wallet_id = wallet_config.wallet_id
            session.language = wallet_config.language
            lnbits_client = LNbitsClient(
                base_url=config.lnbits_url,
                admin_key=wallet_config.admin_key,
                invoice_key=wallet_config.invoice_key,
            )

        menu_handler = MenuHandler(
            session_manager=session_manager,
            lnbits_client=lnbits_client,
            accounts=accounts,
            notifier=notifier,
            lightning_domain=lightning_domain(config.public_url),
            invoice_webhook_url=webhook_url(config) or "",
            country_code=config.default_country_code,
            ussd_code=config.ussd_code,
        )

        # Advance the state machine with the user's latest input.
        menu_response = menu_handler.process_input(session, user_input)
        updated_session = menu_response.updated_session

        if menu_response.continue_session:
            # Persist the updated session and refresh its TTL (Requirement 7.3).
            session_manager.update_session(updated_session)
            prefix = "CON"
        else:
            # Terminal screen: clear all session data (Requirement 8.7).
            session_manager.end_session(updated_session.session_id)
            prefix = "END"

        return f"{prefix} {menu_response.display_text}"

    except SessionStoreError as e:
        # Redis connection or operation failed (Requirement 9.1, 9.3)
        log_api_error(logger, 'redis', 'session_operation', error_message=str(e))
        return "END Service temporarily unavailable. Please try again."
    except Exception as e:
        # Catch-all for unexpected errors to prevent crashes
        logger.error(
            f"Unexpected error during USSD request: {e}",
            extra={
                'event_type': 'unexpected_error',
                'session_id': session_id,
                'phone_number': phone_number
            },
            exc_info=True
        )
        return "END Service error. Please try again later."


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
