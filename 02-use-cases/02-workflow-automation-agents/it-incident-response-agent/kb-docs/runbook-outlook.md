# Runbook: Outlook / Exchange Issues

## Symptoms
- Cannot send or receive emails
- Calendar sync issues
- Outlook crashes or hangs
- "Cannot connect to server" errors

## Diagnosis Steps
1. Check outlook-exchange service status
2. Verify user's email_retention_days quota
3. Check if user's mailbox is over size limit
4. Verify network connectivity (VPN if remote)

## Resolution Actions

### Calendar sync delay (mobile)
- Known issue: mobile clients may lag by up to 5 minutes
- No action required — inform user this is expected behavior
- Action key: `acknowledge_known_delay`

### Mailbox over size limit
- Trigger archival of old items exceeding retention policy
- Action key: `trigger_mailbox_archive`
- This moves items older than email_retention_days to archive

### Cannot connect to server
- Usually a VPN/network issue — redirect to VPN runbook
- If VPN is confirmed working, escalate to CommOps
- Action key: `escalate_exchange_connectivity`

## Escalation Criteria
- If outlook-exchange status is not "operational" → immediate escalation
- If issue persists after standard troubleshooting → escalate to CommOps
