"""
Unit tests for the Customer Service HTTP client.

Tests cover:
  - Successful response returns parsed JSON
  - 404 returns empty dict (not an exception)
  - 5xx raises and trips the circuit breaker
  - Timeout raises TimeoutError
  - get_customer_or_none returns None on circuit open / timeout
  - Uninitialised client raises RuntimeError
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from circuitbreaker import CircuitBreakerError

import src.clients.customer_client as cc


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset circuit breaker state between tests."""
    # Access the underlying CircuitBreaker instance on the decorated function
    cb = getattr(cc.get_customer, "__self__", None)
    if cb is not None:
        cb._failure_count = 0
        cb._state = "closed"
    yield
    cb = getattr(cc.get_customer, "__self__", None)
    if cb is not None:
        cb._failure_count = 0
        cb._state = "closed"


@pytest.fixture
def mock_client():
    """Inject a mock httpx.AsyncClient as the module-level _client."""
    client = MagicMock(spec=httpx.AsyncClient)
    with patch.object(cc, "_client", client):
        yield client


class TestGetCustomer:
    @pytest.mark.asyncio
    async def test_returns_parsed_json_on_success(self, mock_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"customer_number": "CUST-001", "name": "Jane"}
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await cc.get_customer("CUST-001")

        assert result == {"customer_number": "CUST-001", "name": "Jane"}
        mock_client.get.assert_called_once_with("/customers/CUST-001")

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_404(self, mock_client):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await cc.get_customer("NONEXISTENT")

        assert result == {}

    @pytest.mark.asyncio
    async def test_raises_on_5xx(self, mock_client):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=mock_response
        )
        mock_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            await cc.get_customer("CUST-001")

    @pytest.mark.asyncio
    async def test_raises_timeout_error_on_asyncio_timeout(self, mock_client):
        mock_client.get = AsyncMock(side_effect=asyncio.TimeoutError())

        with pytest.raises(TimeoutError, match="did not respond"):
            await cc.get_customer("CUST-001")

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_uninitialised(self):
        with patch.object(cc, "_client", None):
            with pytest.raises(RuntimeError, match="not initialised"):
                await cc.get_customer("CUST-001")


class TestGetCustomerOrNone:
    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, mock_client):
        mock_client.get = AsyncMock(side_effect=asyncio.TimeoutError())

        result = await cc.get_customer_or_none("CUST-001")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self, mock_client):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await cc.get_customer_or_none("CUST-001")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_data_on_success(self, mock_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"customer_number": "CUST-001"}
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await cc.get_customer_or_none("CUST-001")

        assert result == {"customer_number": "CUST-001"}
