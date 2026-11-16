"""Unit tests for the health check endpoint."""

import pytest
from unittest.mock import Mock, patch, AsyncMock
import asyncio

from src.main import health_check


@pytest.mark.unit
class TestHealthCheck:
    """Tests for the /health endpoint."""
    
    @pytest.mark.asyncio
    async def test_health_check_all_services_healthy(self):
        """Test health check returns healthy status when both Redis and LNbits are operational."""
        with patch('src.main.redis_client') as mock_redis, \
             patch('src.main.config') as mock_config, \
             patch('src.main.LNbitsClient') as mock_lnbits_class:
            # Mock Redis ping
            mock_redis.ping.return_value = True
            
            # Mock config
            mock_config.lnbits_url = "http://localhost:5000"
            mock_config.lnbits_admin_key = "admin_key"
            mock_config.lnbits_invoice_key = "invoice_key"
            mock_config.lnbits_wallet_id = "wallet_id"
            
            # Mock LNbits client
            mock_lnbits_instance = Mock()
            mock_lnbits_instance.get_balance.return_value = 100000
            mock_lnbits_class.return_value = mock_lnbits_instance
            
            result = await health_check()
            
            assert result["status"] == "healthy"
            assert result["redis"] == "connected"
            assert result["lnbits"] == "connected"
            assert "version" in result
    
    @pytest.mark.asyncio
    async def test_health_check_redis_disconnected(self):
        """Test health check returns 503 when Redis is unavailable."""
        with patch('src.main.redis_client') as mock_redis, \
             patch('src.main.config') as mock_config, \
             patch('src.main.LNbitsClient') as mock_lnbits_class:
            # Mock Redis failure
            mock_redis.ping.side_effect = Exception("Redis connection failed")
            
            # Mock config
            mock_config.lnbits_url = "http://localhost:5000"
            mock_config.lnbits_admin_key = "admin_key"
            mock_config.lnbits_invoice_key = "invoice_key"
            mock_config.lnbits_wallet_id = "wallet_id"
            
            # Mock LNbits client (healthy)
            mock_lnbits_instance = Mock()
            mock_lnbits_instance.get_balance.return_value = 100000
            mock_lnbits_class.return_value = mock_lnbits_instance
            
            result = await health_check()
            
            # When unhealthy, returns a JSONResponse object
            assert result.status_code == 503
            assert result.body is not None
    
    @pytest.mark.asyncio
    async def test_health_check_lnbits_disconnected(self):
        """Test health check returns 503 when LNbits is unavailable."""
        with patch('src.main.redis_client') as mock_redis, \
             patch('src.main.config') as mock_config, \
             patch('src.main.LNbitsClient') as mock_lnbits_class:
            # Mock Redis (healthy)
            mock_redis.ping.return_value = True
            
            # Mock config
            mock_config.lnbits_url = "http://localhost:5000"
            mock_config.lnbits_admin_key = "admin_key"
            mock_config.lnbits_invoice_key = "invoice_key"
            mock_config.lnbits_wallet_id = "wallet_id"
            
            # Mock LNbits failure
            mock_lnbits_instance = Mock()
            mock_lnbits_instance.get_balance.side_effect = Exception("LNbits unavailable")
            mock_lnbits_class.return_value = mock_lnbits_instance
            
            result = await health_check()
            
            # When unhealthy, returns a JSONResponse object
            assert result.status_code == 503
            assert result.body is not None
    
    @pytest.mark.asyncio
    async def test_health_check_both_services_disconnected(self):
        """Test health check returns 503 when both services are unavailable."""
        with patch('src.main.redis_client') as mock_redis, \
             patch('src.main.config') as mock_config, \
             patch('src.main.LNbitsClient') as mock_lnbits_class:
            # Mock Redis failure
            mock_redis.ping.side_effect = Exception("Redis connection failed")
            
            # Mock config
            mock_config.lnbits_url = "http://localhost:5000"
            mock_config.lnbits_admin_key = "admin_key"
            mock_config.lnbits_invoice_key = "invoice_key"
            mock_config.lnbits_wallet_id = "wallet_id"
            
            # Mock LNbits failure
            mock_lnbits_instance = Mock()
            mock_lnbits_instance.get_balance.side_effect = Exception("LNbits unavailable")
            mock_lnbits_class.return_value = mock_lnbits_instance
            
            result = await health_check()
            
            # When unhealthy, returns a JSONResponse object
            assert result.status_code == 503
            assert result.body is not None
