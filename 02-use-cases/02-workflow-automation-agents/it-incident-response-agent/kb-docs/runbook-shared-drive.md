# Runbook: Shared Drive Access Issues

## Symptoms
- "Network path not found" when mapping shared drive
- Slow file access or timeouts
- Permission denied errors
- Drive disconnects after period of inactivity

## Diagnosis Steps
1. Confirm user is connected to corp-vpn (required for remote access)
2. Check shared-drive service status for known issues
3. Verify user's storage quota hasn't been exceeded
4. Check if the SMB gateway is experiencing issues (known issue in us-east-1)

## Resolution Actions

### SMB Gateway timeout (known issue)
- Apply SMB gateway override to route through backup gateway
- Action key: `apply_smb_gateway_override`
- This reroutes the user's SMB connections through the secondary gateway
- Resolution is temporary (24h) — will auto-revert when primary gateway stabilizes

### Quota exceeded
- Check user's shared_drive_gb quota vs current usage
- If exceeded, cannot auto-resolve — escalate for quota increase
- Action key: `escalate_storage_quota`

### Permission errors
- Verify user's AD group membership matches the share's ACL
- If user was recently added to a team, AD replication may take up to 15 minutes
- Action key: `trigger_ad_sync`

## Escalation Criteria
- If shared-drive status is "degraded" → mention the known SMB gateway issue
- If user has recurring drive issues (>= 2 in 30 days) → escalate to StorageOps
