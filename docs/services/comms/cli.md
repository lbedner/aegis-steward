# CLI Commands Reference

The Communications Service provides CLI commands for sending emails, SMS, and making voice calls.

## Command Structure

```bash
{{ project_slug }} comms <channel> <action> [options]
```

## Commands

### Status

Check the configuration status of all communication channels.

```bash
{{ project_slug }} comms status
```

**Output:**
- Email configuration status
- SMS configuration status
- Voice configuration status
- Total services configured

### Providers

Show available communication providers and free tier information.

```bash
{{ project_slug }} comms providers
```

---

## Email Commands

### Send Email

Send an email via Resend.

```bash
{{ project_slug }} comms email send <to> --subject <subject> [--text <text>] [--html <html>]
```

**Arguments:**
- `<to>` - Recipient email address (required)

**Options:**
- `--subject, -s` - Email subject line (required)
- `--text, -t` - Plain text body
- `--html` - HTML body

**Note:** At least one of `--text` or `--html` is required.

**Examples:**

```bash
# Send plain text email
{{ project_slug }} comms email send user@example.com \
  --subject "Welcome!" \
  --text "Thanks for signing up"

# Send HTML email
{{ project_slug }} comms email send user@example.com \
  --subject "Welcome!" \
  --html "<h1>Welcome!</h1><p>Thanks for signing up</p>"

# Send with both text and HTML
{{ project_slug }} comms email send user@example.com \
  --subject "Welcome!" \
  --text "Thanks for signing up" \
  --html "<h1>Welcome!</h1>"
```

**Output:**
```
Email sent successfully!
Message ID: re_123abc
To: user@example.com
Subject: Welcome!
```

---

## SMS Commands

### Send SMS

Send an SMS message via Twilio.

```bash
{{ project_slug }} comms sms send <to> <body>
```

**Arguments:**
- `<to>` - Recipient phone number in E.164 format (required)
- `<body>` - SMS message body (required, max 1600 chars)

**Examples:**

```bash
# Send verification code
{{ project_slug }} comms sms send +15559876543 "Your code is 123456"

# Send notification
{{ project_slug }} comms sms send +15559876543 "Your order has shipped!"
```

**Output:**
```
SMS sent successfully!
Message SID: SM123abc
To: +15559876543
Segments: 1
```

**Note:** Messages over 160 characters will be split into multiple segments.

---

## Voice Commands

### Make Call

Initiate a voice call via Twilio.

```bash
{{ project_slug }} comms call make <to> <twiml_url> [--timeout <seconds>]
```

**Arguments:**
- `<to>` - Phone number to call in E.164 format (required)
- `<twiml_url>` - URL returning TwiML instructions (required)

**Options:**
- `--timeout, -t` - Seconds to wait for answer (default: 30, range: 5-600)

**Examples:**

```bash
# Make call with default timeout
{{ project_slug }} comms call make +15559876543 "https://example.com/twiml/greeting.xml"

# Make call with custom timeout
{{ project_slug }} comms call make +15559876543 "https://example.com/twiml/survey.xml" --timeout 60
```

**Output:**
```
Call initiated successfully!
Call SID: CA123abc
To: +15559876543
Status: queued
```

---

## Error Handling

All commands exit with code 1 on error and display helpful messages:

**Configuration errors:**
```
Configuration error: RESEND_API_KEY is not set. Sign up at https://resend.com
```

**Provider errors:**
```
Failed to send email: Invalid API key
```

**Validation errors:**
```
Either --text or --html is required
```

---

## Exit Codes

- `0` - Success
- `1` - Error (configuration, provider, or validation)

---

## Environment Variables

The CLI uses the same environment variables as the service:

```bash
# Email
RESEND_API_KEY=re_xxxxxxxxxxxx
RESEND_FROM_EMAIL=noreply@yourdomain.com

# SMS/Voice
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+15551234567
```
