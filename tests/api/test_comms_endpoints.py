"""
Tests for communications service API endpoints.
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def test_client(app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI app.

    No admin gate in non-auth stacks, so no async-db override needed —
    the send endpoints are reachable without a bearer token.
    """
    return TestClient(app)


class TestCommsHealthEndpoints:
    """Test communications service health endpoints."""

    def test_comms_health_endpoint(self, test_client: TestClient) -> None:
        """Test the /comms/health endpoint."""
        response = test_client.get("/api/v1/comms/health")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "communications"
        assert "status" in data
        assert "channels" in data
        assert "email" in data["channels"]
        assert "sms" in data["channels"]
        assert "voice" in data["channels"]

    def test_comms_status_endpoint(self, test_client: TestClient) -> None:
        """Test the /comms/status endpoint."""
        response = test_client.get("/api/v1/comms/status")

        assert response.status_code == 200
        data = response.json()
        assert "email" in data
        assert "sms" in data
        assert "voice" in data

    def test_comms_version_endpoint(self, test_client: TestClient) -> None:
        """Test the /comms/version endpoint."""
        response = test_client.get("/api/v1/comms/version")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "communications"
        assert "version" in data
        assert "features" in data
        assert "providers" in data


class TestEmailSendEndpoint:
    """Test email send endpoint."""

    def test_send_email_success(
        self,
        test_client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Test successful email send via API."""
        with patch("app.components.backend.api.comms.router.send_email") as mock_send:
            from datetime import UTC, datetime

            from app.services.comms.models import EmailResponse, MessageStatus

            mock_send.return_value = EmailResponse(
                id="email-123",
                to=["user@example.com"],
                status=MessageStatus.SENT,
                sent_at=datetime.now(UTC),
            )

            response = test_client.post(
                "/api/v1/comms/email/send",
                json={
                    "to": ["user@example.com"],
                    "subject": "Test Subject",
                    "text": "Test body",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == "email-123"
            assert data["to"] == ["user@example.com"]
            assert data["message"] == "Email sent successfully"

    def test_send_email_validation_error(
        self,
        test_client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Test email send with invalid request."""
        response = test_client.post(
            "/api/v1/comms/email/send",
            json={
                # Missing required fields
                "to": "invalid-email",  # Invalid format
            },
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error


class TestSMSSendEndpoint:
    """Test SMS send endpoint."""

    def test_send_sms_success(
        self,
        test_client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Test successful SMS send via API."""
        with patch("app.components.backend.api.comms.router.send_sms") as mock_send:
            from datetime import UTC, datetime

            from app.services.comms.models import MessageStatus, SMSResponse

            mock_send.return_value = SMSResponse(
                sid="SM123456",
                to="+15559876543",
                status=MessageStatus.SENT,
                sent_at=datetime.now(UTC),
                segments=1,
            )

            response = test_client.post(
                "/api/v1/comms/sms/send",
                json={
                    "to": "+15559876543",
                    "body": "Test SMS message",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["sid"] == "SM123456"
            assert data["to"] == "+15559876543"
            assert data["segments"] == 1


class TestCallMakeEndpoint:
    """Test call make endpoint."""

    def test_make_call_success(
        self,
        test_client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Test successful call initiation via API."""
        with patch("app.components.backend.api.comms.router.make_call") as mock_make:
            from datetime import UTC, datetime

            from app.services.comms.models import CallResponse, CallStatus

            mock_make.return_value = CallResponse(
                sid="CA123456",
                to="+15559876543",
                status=CallStatus.QUEUED,
                started_at=datetime.now(UTC),
            )

            response = test_client.post(
                "/api/v1/comms/call/make",
                json={
                    "to": "+15559876543",
                    "twiml_url": "https://example.com/twiml",
                },
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["sid"] == "CA123456"
            assert data["to"] == "+15559876543"
