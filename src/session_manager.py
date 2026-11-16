"""Session management with Redis backend."""

import json
from typing import Optional
from datetime import datetime

import redis

from src.logging_config import get_logger
from src.models import Session, WalletConfig, MenuState

logger = get_logger(__name__)


class SessionStoreError(Exception):
    """Raised when session store (Redis) operations fail.
    
    This exception is raised for:
    - Redis connection failures
    - Redis timeout errors
    - Redis command execution failures
    - Session data corruption (JSON decode errors)
    """
    pass


class SessionManager:
    """Manages USSD sessions with Redis backend."""
    
    def __init__(self, redis_client: redis.Redis, timeout_seconds: int = 90):
        """
        Initialize SessionManager.
        
        Args:
            redis_client: Redis client instance
            timeout_seconds: Session timeout in seconds (default: 90)
        """
        self.redis = redis_client
        self.timeout = timeout_seconds
        logger.info(
            f"SessionManager initialized with {timeout_seconds}s timeout",
            extra={'event_type': 'initialization', 'session_timeout': timeout_seconds}
        )
    
    def create_session(self, session_id: str, phone_number: str) -> Session:
        """
        Create a new session with initial state.
        
        Args:
            session_id: Unique session identifier
            phone_number: User's phone number
        
        Returns:
            New Session object
        
        Raises:
            SessionStoreError: If Redis operation fails
        """
        try:
            # Look up wallet for this phone number
            wallet_config = self.get_wallet_for_phone(phone_number)
            wallet_id = wallet_config.wallet_id if wallet_config else ""
            
            session = Session(
                session_id=session_id,
                phone_number=phone_number,
                wallet_id=wallet_id,
                current_state=MenuState.MAIN_MENU,
                state_data={},
                created_at=datetime.utcnow(),
                last_activity=datetime.utcnow()
            )
            
            # Store in Redis
            self._store_session(session)
            
            logger.info(
                f"Created session {session_id}",
                extra={
                    'event_type': 'session_created',
                    'session_id': session_id,
                    'phone_number': phone_number
                }
            )
            return session
            
        except redis.RedisError as e:
            logger.error(
                f"Failed to create session: {e}",
                extra={'event_type': 'session_error', 'operation': 'create', 'session_id': session_id}
            )
            raise SessionStoreError(f"Session store unavailable: {e}")
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """
        Retrieve existing session or None if expired/not found.
        
        Args:
            session_id: Session identifier
        
        Returns:
            Session object or None if not found
        
        Raises:
            SessionStoreError: If Redis operation fails
        """
        try:
            key = f"session:{session_id}"
            session_json = self.redis.get(key)
            
            if session_json is None:
                logger.debug(
                    f"Session {session_id} not found (expired or never created)",
                    extra={'event_type': 'session_not_found', 'session_id': session_id}
                )
                return None
            
            session = Session.from_json(json.loads(session_json))
            logger.debug(
                f"Retrieved session {session_id}",
                extra={'event_type': 'session_retrieved', 'session_id': session_id}
            )
            return session
            
        except redis.RedisError as e:
            logger.error(
                f"Failed to retrieve session: {e}",
                extra={'event_type': 'session_error', 'operation': 'retrieve', 'session_id': session_id}
            )
            raise SessionStoreError(f"Session store unavailable: {e}")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(
                f"Failed to deserialize session {session_id}: {e}",
                extra={'event_type': 'session_error', 'operation': 'deserialize', 'session_id': session_id}
            )
            # Return None for corrupted sessions
            return None
    
    def update_session(self, session: Session) -> None:
        """
        Update session state and refresh TTL.
        
        Args:
            session: Session object to update
        
        Raises:
            SessionStoreError: If Redis operation fails
        """
        try:
            # Update last activity timestamp
            session.last_activity = datetime.utcnow()
            
            # Store in Redis
            self._store_session(session)
            
            logger.debug(
                f"Updated session {session.session_id}",
                extra={'event_type': 'session_updated', 'session_id': session.session_id}
            )
            
        except redis.RedisError as e:
            logger.error(
                f"Failed to update session: {e}",
                extra={'event_type': 'session_error', 'operation': 'update', 'session_id': session.session_id}
            )
            raise SessionStoreError(f"Session store unavailable: {e}")
    
    def end_session(self, session_id: str) -> None:
        """
        Explicitly terminate and delete session.
        
        Args:
            session_id: Session identifier to delete
        
        Raises:
            SessionStoreError: If Redis operation fails
        """
        try:
            key = f"session:{session_id}"
            deleted = self.redis.delete(key)
            
            if deleted:
                logger.info(
                    f"Ended session {session_id}",
                    extra={'event_type': 'session_ended', 'session_id': session_id}
                )
            else:
                logger.debug(
                    f"Session {session_id} not found when ending",
                    extra={'event_type': 'session_not_found', 'session_id': session_id}
                )
                
        except redis.RedisError as e:
            logger.error(
                f"Failed to end session: {e}",
                extra={'event_type': 'session_error', 'operation': 'end', 'session_id': session_id}
            )
            raise SessionStoreError(f"Session store unavailable: {e}")
    
    def get_wallet_for_phone(self, phone_number: str) -> Optional[WalletConfig]:
        """
        Look up wallet configuration for phone number.
        
        Args:
            phone_number: User's phone number
        
        Returns:
            WalletConfig or None if not found
        
        Raises:
            SessionStoreError: If Redis operation fails
        """
        try:
            key = f"phone_wallet:{phone_number}"
            wallet_json = self.redis.get(key)
            
            if wallet_json is None:
                logger.debug(
                    "No wallet mapping found for phone",
                    extra={'event_type': 'wallet_not_found', 'phone_number': phone_number}
                )
                return None
            
            wallet_config = WalletConfig.from_json(json.loads(wallet_json))
            logger.debug(
                "Found wallet mapping for phone",
                extra={'event_type': 'wallet_found', 'phone_number': phone_number}
            )
            return wallet_config
            
        except redis.RedisError as e:
            logger.error(
                f"Failed to retrieve wallet mapping: {e}",
                extra={'event_type': 'wallet_error', 'operation': 'retrieve', 'phone_number': phone_number}
            )
            raise SessionStoreError(f"Session store unavailable: {e}")
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(
                f"Failed to deserialize wallet config: {e}",
                extra={'event_type': 'wallet_error', 'operation': 'deserialize', 'phone_number': phone_number}
            )
            return None
    
    def set_wallet_for_phone(self, wallet_config: WalletConfig) -> None:
        """
        Store wallet configuration for phone number.
        
        Args:
            wallet_config: Wallet configuration to store
        
        Raises:
            SessionStoreError: If Redis operation fails
        """
        try:
            key = f"phone_wallet:{wallet_config.phone_number}"
            wallet_json = json.dumps(wallet_config.to_json())
            
            # Store without expiry (persistent mapping)
            self.redis.set(key, wallet_json)
            
            logger.info(
                "Stored wallet mapping for phone",
                extra={'event_type': 'wallet_stored', 'phone_number': wallet_config.phone_number}
            )
            
        except redis.RedisError as e:
            logger.error(
                f"Failed to store wallet mapping: {e}",
                extra={'event_type': 'wallet_error', 'operation': 'store', 'phone_number': wallet_config.phone_number}
            )
            raise SessionStoreError(f"Session store unavailable: {e}")
    
    def _store_session(self, session: Session) -> None:
        """
        Internal method to store session in Redis with TTL.
        
        Args:
            session: Session to store
        
        Raises:
            redis.RedisError: If Redis operation fails
        """
        key = f"session:{session.session_id}"
        session_json = json.dumps(session.to_json())
        
        # Store with TTL
        self.redis.setex(key, self.timeout, session_json)
