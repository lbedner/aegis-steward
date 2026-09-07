# API Reference

The Communications Service provides REST API endpoints for sending emails, SMS, and making voice calls.

## Base URL

```
/api/v1/comms
```

---

## Email Endpoints

### Send Email

Send an email via Resend.

**Endpoint:** `POST /comms/email/send`

**Request Body:**

```json
{
  "to": ["user@example.com"],
  "subject": "Welcome!",
  "text": "Thanks for signing up",
  "html": "<h1>Welcome!</h1>",
  "from_email": "noreply@yourdomain.com",
  "reply_to": ["support@yourdomain.com"],
  "cc": ["manager@example.com"],
  "bcc": ["logs@example.com"],
  "tags": ["welcome", "onboarding"]
}
```

**Required Fields:**
- `to` - Array of recipient emails
- `subject` - Email subject
- At least one of `text` or `html`

**Optional Fields:**
- `from_email` - Sender email (defaults to `RESEND_FROM_EMAIL`)
- `reply_to` - Reply-to addresses
- `cc` - Carbon copy recipients
- `bcc` - Blind carbon copy recipients
- `tags` - Tags for tracking

**Response (200):**

```json
{
  "id": "re_123abc",
  "to": ["user@example.com"],
  "status": "sent",
  "message": "Email sent successfully"
}
```

**Errors:**
- `422` - Validation error
- `502` - Provider error
- `503` - Service not configured

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/comms/email/send \
  -H "Content-Type: application/json" \
  -d '{
    "to": ["user@example.com"],
    "subject": "Test Email",
    "text": "Hello from Aegis Stack!"
  }'
```

---

## SMS Endpoints

### Send SMS

Send an SMS message via Twilio.

**Endpoint:** `POST /comms/sms/send`

**Request Body:**

```json
{
  "to": "+15559876543",
  "body": "Your verification code is 123456",
  "from_number": "+15551234567",
  "status_callback": "https://example.com/webhook/sms"
}
```

**Required Fields:**
- `to` - Recipient phone number (E.164 format)
- `body` - Message content (max 1600 chars)

**Optional Fields:**
- `from_number` - Sender phone number (defaults to `TWILIO_PHONE_NUMBER`)
- `status_callback` - URL for status updates

**Response (200):**

```json
{
  "sid": "SM123abc",
  "to": "+15559876543",
  "status": "sent",
  "segments": 1,
  "message": "SMS sent successfully"
}
```

**Errors:**
- `422` - Validation error
- `502` - Provider error
- `503` - Service not configured

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/comms/sms/send \
  -H "Content-Type: application/json" \
  -d '{
    "to": "+15559876543",
    "body": "Your code is 123456"
  }'
```

---

## Voice Endpoints

### Make Call

Initiate a voice call via Twilio.

**Endpoint:** `POST /comms/call/make`

**Request Body:**

```json
{
  "to": "+15559876543",
  "twiml_url": "https://example.com/twiml/greeting.xml",
  "from_number": "+15551234567",
  "status_callback": "https://example.com/webhook/call",
  "timeout": 30
}
```

**Required Fields:**
- `to` - Phone number to call (E.164 format)
- `twiml_url` - URL returning TwiML instructions

**Optional Fields:**
- `from_number` - Caller ID (defaults to `TWILIO_PHONE_NUMBER`)
- `status_callback` - URL for call status updates
- `timeout` - Seconds to wait for answer (5-600, default: 30)

**Response (200):**

```json
{
  "sid": "CA123abc",
  "to": "+15559876543",
  "status": "queued",
  "message": "Call initiated successfully"
}
```

**Errors:**
- `422` - Validation error
- `502` - Provider error
- `503` - Service not configured

**Example:**

```bash
curl -X POST http://localhost:8000/api/v1/comms/call/make \
  -H "Content-Type: application/json" \
  -d '{
    "to": "+15559876543",
    "twiml_url": "https://example.com/twiml/greeting.xml"
  }'
```

---

## Status Endpoints

### Health Check

Get health status of all communication channels.

**Endpoint:** `GET /comms/health`

**Response (200):**

```json
{
  "service": "communications",
  "status": "healthy",
  "channels": {
    "email": {
      "configured": true,
      "provider": "resend",
      "errors": []
    },
    "sms": {
      "configured": true,
      "provider": "twilio",
      "errors": []
    },
    "voice": {
      "configured": true,
      "provider": "twilio",
      "errors": []
    }
  },
  "total_errors": 0
}
```

### Service Status

Get detailed configuration status.

**Endpoint:** `GET /comms/status`

**Response (200):**

```json
{
  "email": {
    "service": "email",
    "provider": "resend",
    "configured": true,
    "api_key_set": true,
    "from_email_set": true,
    "from_email": "noreply@yourdomain.com"
  },
  "sms": {
    "service": "sms",
    "provider": "twilio",
    "configured": true,
    "account_sid_set": true,
    "auth_token_set": true,
    "phone_number_set": true,
    "phone_number": "+15551234567"
  },
  "voice": {
    "service": "voice",
    "provider": "twilio",
    "configured": true,
    "account_sid_set": true,
    "auth_token_set": true,
    "phone_number_set": true,
    "phone_number": "+15551234567"
  }
}
```

### Version Info

Get service version and features.

**Endpoint:** `GET /comms/version`

**Response (200):**

```json
{
  "service": "communications",
  "version": "1.0",
  "features": [
    "email_send",
    "sms_send",
    "voice_call",
    "webhook_handlers"
  ],
  "providers": {
    "email": "resend",
    "sms": "twilio",
    "voice": "twilio"
  },
  "endpoints": [
    "POST /comms/email/send",
    "POST /comms/sms/send",
    "POST /comms/call/make",
    "GET /comms/health",
    "GET /comms/status"
  ]
}
```

---

## Error Responses

All errors follow this format:

```json
{
  "detail": "Error message here"
}
```

**HTTP Status Codes:**
- `400` - Bad request
- `422` - Validation error
- `500` - Unexpected server error
- `502` - Provider error (Resend/Twilio issue)
- `503` - Service not configured
