# Incident Playbooks

This document contains incident response playbooks for various types of operational issues.

## High Memory Usage Incident Response

**Playbook ID:** `memory-pressure-playbook`  
**Incident Type:** Performance  
**Severity:** High  
**Estimated Resolution Time:** 15-30 minutes

### Description
Procedure for handling high memory usage incidents

### Triggers
- Memory utilization > 85%
- OutOfMemoryError in logs
- Pod evictions due to memory pressure

### Response Steps
1. Identify affected pods using `kubectl get pods --field-selector=status.phase=Running`
2. Check memory usage: `kubectl top pods -n production`
3. Review recent memory metrics and trends
4. Scale up deployment if horizontal scaling is possible
5. Increase memory limits in deployment configuration
6. Restart affected pods if necessary
7. Monitor recovery and validate normal operation

### Escalation
- **Primary:** on-call-engineer
- **Secondary:** platform-team
- **Manager:** engineering-manager

### Related Runbooks
- pod-crashloop-troubleshooting
- resource-optimization

---

## Database Connection Failure Response

**Playbook ID:** `database-connection-failure`  
**Incident Type:** Availability  
**Severity:** Critical  
**Estimated Resolution Time:** 5-15 minutes

### Description
Procedure for handling database connectivity issues

### Triggers
- Database connection timeout errors
- Connection pool exhaustion
- Database pods in CrashLoopBackOff

### Response Steps
1. Check database pod status: `kubectl get pods -l app=database`
2. Review database logs: `kubectl logs -f database-pod-name`
3. Verify database service endpoints
4. Check network connectivity between services
5. Restart database pod if configuration is correct
6. Scale connection pool if needed
7. Verify application can connect to database

### Escalation
- **Primary:** database-admin
- **Secondary:** infrastructure-team
- **Manager:** site-reliability-manager

### Related Runbooks
- database-recovery
- connection-pool-tuning

---

## High Error Rate Response

**Playbook ID:** `high-error-rate-response`  
**Incident Type:** Availability  
**Severity:** High  
**Estimated Resolution Time:** 10-20 minutes

### Description
Procedure for handling increased error rates

### Triggers
- Error rate > 10%
- 5xx errors increasing
- Multiple service failures

### Response Steps
1. Check current error rates across all services
2. Identify the source service causing errors
3. Review application logs for error patterns
4. Check recent deployments or configuration changes
5. Consider rolling back if recent deployment
6. Scale affected services if load-related
7. Enable circuit breakers if cascading failures

### Escalation
- **Primary:** on-call-engineer
- **Secondary:** service-owner
- **Manager:** engineering-manager

### Related Runbooks
- rollback-procedures
- circuit-breaker-configuration

---

## Pod Startup Failure Resolution

**Playbook ID:** `pod-startup-failure`  
**Incident Type:** Deployment  
**Severity:** Medium  
**Estimated Resolution Time:** 10-30 minutes

### Description
Procedure for resolving pod startup issues

### Triggers
- Pods stuck in Pending state
- ImagePullBackOff errors
- Init container failures

### Response Steps
1. Check pod events: `kubectl describe pod <pod-name>`
2. Verify image availability and pull secrets
3. Check resource quotas and limits
4. Review init container logs if applicable
5. Verify configuration maps and secrets
6. Check node resources and scheduling constraints
7. Recreate pod with corrected configuration

### Escalation
- **Primary:** platform-team
- **Secondary:** infrastructure-team
- **Manager:** platform-manager

### Related Runbooks
- kubernetes-troubleshooting
- deployment-best-practices

---

## Database Pod CrashLoopBackOff Incident

**Playbook ID:** `database-pod-crashloop-incident`  
**Incident Type:** Availability  
**Severity:** Critical  
**Estimated Resolution Time:** 10-15 minutes  
**Specific Pod:** `database-pod-7b9c4d8f2a-x5m1q`

### Description
Critical incident response for database pod continuously crashing

### Root Cause
Missing ConfigMap 'database-config' preventing PostgreSQL initialization

### Triggers
- Database pod in CrashLoopBackOff state
- ConfigMap 'database-config' not found errors
- PostgreSQL initialization failures
- Volume mount permission errors

### Response Steps
1. **IMMEDIATE:** Check pod status: `kubectl get pod database-pod-7b9c4d8f2a-x5m1q -n production`
2. Review pod logs: `kubectl logs database-pod-7b9c4d8f2a-x5m1q --previous -n production`
3. Verify ConfigMap existence: `kubectl get configmap database-config -n production`
4. If ConfigMap missing, create it:
   ```bash
   kubectl create configmap database-config \
     --from-literal=database.conf='shared_buffers=256MB\nmax_connections=100\nlog_destination=stderr' \
     -n production
   ```
5. Check volume permissions: `kubectl exec -it database-pod-7b9c4d8f2a-x5m1q -- ls -la /var/lib/postgresql/`
6. Force pod restart: `kubectl delete pod database-pod-7b9c4d8f2a-x5m1q -n production`
7. Monitor pod startup: `kubectl logs database-pod-7b9c4d8f2a-x5m1q -f -n production`
8. Verify database connectivity once running

### Impact Assessment
- **Services Affected:** web-service, api-service
- **Users Affected:** All users - complete database outage
- **Business Impact:** Critical - No data operations possible

### Escalation
- **Primary:** database-oncall@company.com
- **Secondary:** platform-oncall@company.com
- **Manager:** incident-manager@company.com
- **Escalation Time:** 5 minutes

### Related Runbooks
- database-crashloop-troubleshooting
- configmap-management

### Post-Incident Actions
- Add ConfigMap to deployment manifest
- Implement ConfigMap validation in CI/CD
- Add monitoring for ConfigMap existence
- Document configuration requirements