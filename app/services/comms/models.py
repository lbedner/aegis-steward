"""
Communications service data models and enums.

This module defines the core data structures for communications service
including request/response models for email, SMS, and voice calls.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class MessageStatus(str, Enum):
    """Status of message delivery."""

    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    BOUNCED = "bounced"


class CallStatus(str, Enum):
    """Status of voice call."""

    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    BUSY = "busy"
    FAILED = "failed"
    NO_ANSWER = "no-answer"
    CANCELED = "canceled"


# Email Models


class SendEmailRequest(BaseModel):
    """Request model for sending an email."""

    to: list[EmailStr] = Field(..., description="List of recipient email addresses")
    subject: str = Field(..., description="Email subject line")
    text: str | None = Field(None, description="Plain text body")
    html: str | None = Field(None, description="HTML body")
    from_email: str | None = Field(
        None, description="Sender email (defaults to configured from_email)"
    )
    reply_to: list[EmailStr] | None = Field(None, description="Reply-to addresses")
    cc: list[EmailStr] | None = Field(None, description="CC recipients")
    bcc: list[EmailStr] | None = Field(None, description="BCC recipients")
    tags: list[str] | None = Field(None, description="Email tags for tracking")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Custom metadata"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "to": ["user@example.com"],
                "subject": "Welcome to Aegis Stack",
                "text": "Thank you for signing up!",
                "html": "<h1>Welcome!</h1><p>Thank you for signing up!</p>",
            }
        }


class EmailResponse(BaseModel):
    """Response model for email operations."""

    id: str = Field(..., description="Resend message ID")
    to: list[str] = Field(..., description="Recipients")
    status: MessageStatus = Field(default=MessageStatus.SENT)
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Config:
        use_enum_values = True


# SMS Models


class SendSMSRequest(BaseModel):
    """Request model for sending an SMS."""

    to: str = Field(
        ..., description="Recipient phone number in E.164 format (+1234567890)"
    )
    body: str = Field(..., description="SMS message body", max_length=1600)
    from_number: str | None = Field(
        None, description="Sender phone number (defaults to configured number)"
    )
    status_callback: str | None = Field(
        None, description="URL to receive status updates"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Custom metadata"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "to": "+15551234567",
                "body": "Your verification code is 123456",
            }
        }


class SMSResponse(BaseModel):
    """Response model for SMS operations."""

    sid: str = Field(..., description="Twilio message SID")
    to: str = Field(..., description="Recipient phone number")
    status: MessageStatus = Field(default=MessageStatus.SENT)
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    segments: int = Field(default=1, description="Number of SMS segments")

    class Config:
        use_enum_values = True


# Voice Call Models


class MakeCallRequest(BaseModel):
    """Request model for making a voice call."""

    to: str = Field(
        ..., description="Recipient phone number in E.164 format (+1234567890)"
    )
    twiml_url: str = Field(..., description="URL returning TwiML instructions")
    from_number: str | None = Field(
        None, description="Caller ID phone number (defaults to configured number)"
    )
    status_callback: str | None = Field(
        None, description="URL to receive call status updates"
    )
    timeout: int = Field(
        default=30, description="Seconds to wait for answer", ge=5, le=600
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Custom metadata"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "to": "+15551234567",
                "twiml_url": "https://example.com/twiml/greeting.xml",
            }
        }


class CallResponse(BaseModel):
    """Response model for voice call operations."""

    sid: str = Field(..., description="Twilio call SID")
    to: str = Field(..., description="Called phone number")
    status: CallStatus = Field(default=CallStatus.QUEUED)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration: int | None = Field(None, description="Call duration in seconds")

    class Config:
        use_enum_values = True


# Webhook Models


class EmailWebhookEvent(BaseModel):
    """Webhook event for email status updates from Resend."""

    type: str = Field(..., description="Event type (delivered, bounced, etc.)")
    created_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class SMSWebhookEvent(BaseModel):
    """Webhook event for SMS status updates from Twilio."""

    message_sid: str = Field(..., description="Twilio message SID")
    message_status: str = Field(..., description="Message status")
    to: str = Field(..., description="Recipient phone number")
    from_number: str = Field(..., alias="from", description="Sender phone number")
    error_code: str | None = Field(None, description="Error code if failed")
    error_message: str | None = Field(None, description="Error message if failed")


class CallWebhookEvent(BaseModel):
    """Webhook event for call status updates from Twilio."""

    call_sid: str = Field(..., description="Twilio call SID")
    call_status: str = Field(..., description="Call status")
    to: str = Field(..., description="Called phone number")
    from_number: str = Field(..., alias="from", description="Caller phone number")
    duration: int | None = Field(None, description="Call duration in seconds")
    recording_url: str | None = Field(None, description="URL to call recording")


# Service Status Models


class CommsServiceStatus(BaseModel):
    """Status information for communications service."""

    email_configured: bool = Field(
        default=False, description="Whether Resend is configured"
    )
    sms_configured: bool = Field(
        default=False, description="Whether Twilio SMS is configured"
    )
    voice_configured: bool = Field(
        default=False, description="Whether Twilio Voice is configured"
    )
    resend_api_key_set: bool = Field(
        default=False, description="Whether RESEND_API_KEY is set"
    )
    twilio_credentials_set: bool = Field(
        default=False, description="Whether Twilio credentials are set"
    )
