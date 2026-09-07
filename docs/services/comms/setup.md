# Provider Setup Guide

This guide walks you through setting up Resend and Twilio for the Communications Service.

## Resend (Email)

### Sign Up

1. Go to [resend.com](https://resend.com)
2. Sign up for a free account
3. Verify your email address

### Free Tier

- **100 emails/day**
- **3,000 emails/month**
- No credit card required

### Get API Key

1. Go to [resend.com/api-keys](https://resend.com/api-keys)
2. Click "Create API Key"
3. Name it (e.g., "aegis-dev")
4. Copy the key (starts with `re_`)

### Configure Domain (Production)

For production use, you need to verify a domain:

1. Go to [resend.com/domains](https://resend.com/domains)
2. Click "Add Domain"
3. Add the DNS records shown
4. Wait for verification (usually a few minutes)

For development, you can use `onboarding@resend.dev` as the from address.

### Environment Variables

```bash
# Required
RESEND_API_KEY=re_xxxxxxxxxxxx

# Required for production (use your verified domain)
RESEND_FROM_EMAIL=noreply@yourdomain.com

# For development (Resend's test domain)
RESEND_FROM_EMAIL=onboarding@resend.dev
```

## Twilio (SMS & Voice)

### Sign Up

1. Go to [twilio.com/try-twilio](https://twilio.com/try-twilio)
2. Sign up for a free trial
3. Verify your phone number

### Free Trial

- **$15 USD trial credit**
- SMS: ~$0.0079/message (US)
- Voice: ~$0.0085/minute (US)
- Trial numbers show "Sent from Twilio" prefix

### Get Credentials

1. Go to [console.twilio.com](https://console.twilio.com)
2. Find your **Account SID** (starts with `AC`)
3. Find your **Auth Token** (click to reveal)

### Get a Phone Number

1. Go to Phone Numbers > Buy a Number
2. Search for a number in your country
3. Select capabilities needed (SMS, Voice)
4. Purchase with trial credit

### Environment Variables

```bash
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+15551234567  # E.164 format
```

## Verify Configuration

After setting up your environment variables, verify everything works:

```bash
# Check all services status
{{ project_slug }} comms status

# Expected output:
# Communications Service Status
# ==================================================
#
# Email (Resend)
#   Status: Configured
#   API Key: Set
#   From Email: noreply@yourdomain.com
#
# SMS (Twilio)
#   Status: Configured
#   Account SID: Set
#   Auth Token: Set
#   Phone Number: +15551234567
#
# Voice (Twilio)
#   Status: Configured
#   ...
#
# ==================================================
# 3/3 services configured
```

## Test Messages

### Test Email

```bash
{{ project_slug }} comms email send your-email@example.com \
  --subject "Test from Aegis Stack" \
  --text "This is a test email!"
```

### Test SMS

```bash
# Note: Trial accounts can only send to verified numbers
{{ project_slug }} comms sms send "+1YOUR_VERIFIED_NUMBER" "Test from Aegis Stack"
```

## Troubleshooting

### Common Issues

**Email not delivered**
- Check spam folder
- Verify domain DNS records are correct
- Ensure from address matches verified domain

**SMS fails with "unverified number"**
- Trial accounts can only send to verified numbers
- Add numbers at console.twilio.com/verified-caller-ids
- Upgrade to paid account for unrestricted sending

**API key invalid**
- Ensure no extra spaces in environment variables
- Regenerate API key if compromised
- Check key hasn't been revoked

### Getting Help

- Resend: [resend.com/docs](https://resend.com/docs)
- Twilio: [twilio.com/docs](https://twilio.com/docs)
