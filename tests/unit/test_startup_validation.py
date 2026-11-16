"""Unit tests for startup configuration validation."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from src.main import startup_event


@pytest.mark.unit
class TestStartupValidation:
    """Tests for startup configuration validation (Task 11.3)."""
    
    @pytest.mark.asyncio
    async def test_startup_validates_redis_connectivity(self):
        """Test startup fails fast when Redis is unreachable."""
        with patch('src.main.ConfigManager') as mock_config_manager, \
             patch('src.main.redis') as mock_redis_module:
            # Mock config loading
            mock_config = Mock()
            mock_config.redis_url = "redis://localhost:6379"
            mock_config.session_timeout = 90
            mock_config.lnbits_url = "http://localhost:5000"
            mock_config.lnbits_admin_key = "admin_key"
            mock_config.lnbits_invoice_key = "invoice_key"
            mock_config.lnbits_wallet_id = "wallet_id"
            mock_config_manager.load_from_env.return_value = mock_config
            mock_config_manager.format_config.return_value = "Config loaded"
            
            # Mock Redis connection that fails ping
            mock_redis_client = Mock()
            mock_redis_client.ping.side_effect = Exception("Redis connection failed")
            mock_redis_module.from_url.return_value = mock_redis_client
            
            # Startup should raise RuntimeError when Redis is unreachable
            with pytest.raises(RuntimeError, match="Cannot connect to Redis"):
                await startup_event()
    
    @pytest.mark.asyncio
    async def test_startup_validates_lnbits_connectivity(self):
        """Test startup fails fast when LNbits is unreachable."""
        with patch('src.main.ConfigManager') as mock_config_manager, \
             patch('src.main.redis') as mock_redis_module, \
             patch('src.main.SessionManager') as mock_session_manager, \
             patch('src.main.LNbitsClient') as mock_lnbits_class:
            # Mock config loading
            mock_config = Mock()
            mock_config.redis_url = "redis://localhost:6379"
            mock_config.session_timeout = 90
            mock_config.lnbits_url = "http://localhost:5000"
            mock_config.lnbits_admin_key = "admin_key"
            mock_config.lnbits_invoice_key = "invoice_key"
            mock_config.lnbits_wallet_id = "wallet_id"
            mock_config_manager.load_from_env.return_value = mock_config
            mock_config_manager.format_config.return_value = "Config loaded"
            
            # Mock Redis connection (healthy)
            mock_redis_client = Mock()
            mock_redis_client.ping.return_value = True
            mock_redis_module.from_url.return_value = mock_redis_client
            
            # Mock LNbits client that fails connectivity check
            mock_lnbits_instance = Mock()
            mock_lnbits_instance.get_balance.side_effect = Exception("LNbits unavailable")
            mock_lnbits_class.return_value = mock_lnbits_instance
            
            # Startup should raise RuntimeError when LNbits is unreachable
            with pytest.raises(RuntimeError, match="Cannot connect to LNbits"):
                await startup_event()
    
    @pytest.mark.asyncio
    async def test_startup_succeeds_when_all_services_available(self):
        """Test startup succeeds when both Redis and LNbits are available."""
        with patch('src.main.ConfigManager') as mock_config_manager, \
             patch('src.main.redis') as mock_redis_module, \
             patch('src.main.SessionManager') as mock_session_manager, \
             patch('src.main.LNbitsClient') as mock_lnbits_class:
            # Mock config loading
            mock_config = Mock()
            mock_config.redis_url = "redis://localhost:6379"
            mock_config.session_timeout = 90
            mock_config.lnbits_url = "http://localhost:5000"
            mock_config.lnbits_admin_key = "admin_key"
            mock_config.lnbits_invoice_key = "invoice_key"
            mock_config.lnbits_wallet_id = "wallet_id"
            mock_config_manager.load_from_env.return_value = mock_config
            mock_config_manager.format_config.return_value = "Config loaded"
            
            # Mock Redis connection (healthy)
            mock_redis_client = Mock()
            mock_redis_client.ping.return_value = True
            mock_redis_module.from_url.return_value = mock_redis_client
            
            # Mock LNbits client (healthy)
            mock_lnbits_instance = Mock()
            mock_lnbits_instance.get_balance.return_value = 100000
            mock_lnbits_class.return_value = mock_lnbits_instance
            
            # Startup should succeed without raising
            await startup_event()
            
            # Verify connectivity checks were called
            mock_redis_client.ping.assert_called_once()
            mock_lnbits_instance.get_balance.assert_called_once_with("wallet_id")
    
    @pytest.mark.asyncio
    async def test_startup_logs_configuration_status(self):
        """Test startup logs configuration and connectivity status."""
        with patch('src.main.ConfigManager') as mock_config_manager, \
             patch('src.main.redis') as mock_redis_module, \
             patch('src.main.SessionManager') as mock_session_manager, \
             patch('src.main.LNbitsClient') as mock_lnbits_class, \
             patch('src.main.logger') as mock_logger:
            # Mock config loading
            mock_config = Mock()
            mock_config.redis_url = "redis://localhost:6379"
            mock_config.session_timeout = 90
            mock_config.lnbits_url = "http://localhost:5000"
            mock_config.lnbits_admin_key = "admin_key"
            mock_config.lnbits_invoice_key = "invoice_key"
            mock_config.lnbits_wallet_id = "wallet_id"
            mock_config_manager.load_from_env.return_value = mock_config
            mock_config_manager.format_config.return_value = "Config loaded"
            
            # Mock Redis connection (healthy)
            mock_redis_client = Mock()
            mock_redis_client.ping.return_value = True
            mock_redis_module.from_url.return_value = mock_redis_client
            
            # Mock LNbits client (healthy)
            mock_lnbits_instance = Mock()
            mock_lnbits_instance.get_balance.return_value = 100000
            mock_lnbits_class.return_value = mock_lnbits_instance
            
            # Run startup
            await startup_event()
            
            # Verify proper logging occurred
            # Check that configuration was logged
            assert any("Configuration loaded successfully" in str(call) 
                      for call in mock_logger.info.call_args_list)
            
            # Check that Redis validation was logged
            assert any("Redis connectivity validated" in str(call) 
                      for call in mock_logger.info.call_args_list)
            
            # Check that LNbits validation was logged
            assert any("LNbits API connectivity validated" in str(call) 
                      for call in mock_logger.info.call_args_list)
            
            # Check that startup complete message was logged
            assert any("Gateway startup complete" in str(call) 
                      for call in mock_logger.info.call_args_list)


@pytest.mark.unit
class TestCallbackSecretRequired:
    """Production (DEMO_MODE off) refuses to start without USSD_CALLBACK_SECRET."""

    def _config(self, secret):
        cfg = Mock()
        cfg.redis_url = "redis://localhost:6379"
        cfg.session_timeout = 90
        cfg.lnbits_url = "http://localhost:5000"
        cfg.ussd_callback_secret = secret
        return cfg

    @pytest.mark.asyncio
    async def test_missing_secret_blocks_production_startup(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "false")
        with patch('src.main.ConfigManager') as cm, patch('src.main.redis') as r:
            cm.load_from_env.return_value = self._config("")
            cm.format_config.return_value = "Config loaded"
            with pytest.raises(RuntimeError, match="USSD_CALLBACK_SECRET is required"):
                await startup_event()
            r.from_url.return_value.ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_secret_allowed_in_demo_mode(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "true")
        with patch('src.main.ConfigManager') as cm, patch('src.main.redis') as r:
            cm.load_from_env.return_value = self._config("")
            cm.format_config.return_value = "Config loaded"
            r.from_url.return_value.ping.side_effect = Exception("stop here")
            # Gets past the secret check and fails later, on Redis.
            with pytest.raises(RuntimeError, match="Cannot connect to Redis"):
                await startup_event()
