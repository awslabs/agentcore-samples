# Runbook: VPN Connectivity Issues

## Symptoms
- User cannot connect to corp-vpn
- VPN connects but internal resources unreachable
- "Network path not found" when accessing file shares over VPN
- Intermittent disconnections

## Diagnosis Steps
1. Verify the user's VPN client version (minimum 4.2.0 required)
2. Check if the user is within their session quota (check quotas.vpn_sessions)
3. Check corp-vpn service status for known outages
4. Verify DNS resolution is working (nslookup files.acme.corp)

## Resolution Actions

### Split-tunnel DNS issue (macOS 14.5+)
- Apply the DNS override configuration via change request
- Action key: `apply_dns_override`
- This forces internal DNS through the VPN tunnel

### Session quota exceeded
- If user has hit vpn_sessions limit, cannot resolve automatically
- Escalate to NetOps for quota increase approval
- Action key: `escalate_quota_increase`

### VPN reconnect loops
- Clear VPN session state via change request
- Action key: `reset_vpn_session`
- User should disconnect, wait 30 seconds, reconnect

## Escalation Criteria
- If user has >= 3 incidents in 30 days → escalate to NetOps manager
- If corp-vpn status is "degraded" or "down" → link to incident channel
