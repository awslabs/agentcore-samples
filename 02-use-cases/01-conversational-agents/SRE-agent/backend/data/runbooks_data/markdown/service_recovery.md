# Service Recovery Procedures

This document contains recovery procedures for various services and complete stack recovery processes.

## Web Service Recovery

**Recovery ID:** `web-service-recovery`  
**Service:** web-service

### Recovery Steps

#### Step 1: Check Service Health
- **Command:** `kubectl get pods -l app=web-app`
- **Expected Result:** All pods should be in Running state

#### Step 2: Restart Unhealthy Pods
- **Command:** `kubectl delete pod <unhealthy-pod-name>`
- **Expected Result:** New pod should start and become ready

#### Step 3: Scale Deployment if Needed
- **Command:** `kubectl scale deployment web-app-deployment --replicas=5`
- **Expected Result:** Additional pods should start

#### Step 4: Verify Load Balancer
- **Command:** `kubectl get svc web-app-service`
- **Expected Result:** External IP should be assigned

#### Step 5: Test Service Endpoints
- **Command:** `curl http://<external-ip>/health`
- **Expected Result:** Should return 200 OK

### Rollback Procedure

**Trigger:** If recovery fails after 30 minutes

#### Rollback Steps
1. Get previous deployment revision: `kubectl rollout history deployment/web-app-deployment`
2. Rollback to previous version: `kubectl rollout undo deployment/web-app-deployment`
3. Monitor rollback status: `kubectl rollout status deployment/web-app-deployment`
4. Verify service health after rollback

---

## Database Recovery

**Recovery ID:** `database-recovery`  
**Service:** database

### Recovery Steps

#### Step 1: Check Database Pod Status
- **Command:** `kubectl get pods -l app=database`
- **Expected Result:** Pod should be running

#### Step 2: Verify Persistent Volume
- **Command:** `kubectl get pv,pvc -n production`
- **Expected Result:** PVC should be bound

#### Step 3: Check Database Logs
- **Command:** `kubectl logs -f database-pod-name`
- **Expected Result:** No critical errors

#### Step 4: Test Database Connectivity
- **Command:** `kubectl exec -it database-pod -- psql -U postgres -c 'SELECT 1'`
- **Expected Result:** Query should return successfully

#### Step 5: Verify Replication if Applicable
- **Command:** `kubectl exec -it database-pod -- psql -U postgres -c 'SELECT * FROM pg_stat_replication'`
- **Expected Result:** Replicas should be connected

### Data Recovery

**Backup Location:** s3://backup-bucket/database/

#### Restore Procedure
1. Stop application writes
2. Create new database pod with empty volume
3. Restore from latest backup: `pg_restore -d dbname backup.dump`
4. Verify data integrity
5. Resume application traffic

---

## Complete Stack Recovery

**Recovery ID:** `full-stack-recovery`  
**Title:** Complete Stack Recovery

### Service Priority Order
1. database
2. cache-service
3. api-service
4. web-service
5. ingress-controller

### Pre-Recovery Checks
- Verify cluster health: `kubectl get nodes`
- Check resource availability: `kubectl top nodes`
- Review recent events: `kubectl get events --sort-by=.metadata.creationTimestamp`

### Recovery Phases

#### Phase 1: Infrastructure
- Verify node health
- Check network connectivity
- Ensure storage availability

#### Phase 2: Data Layer
- Recover database services
- Verify data integrity
- Restore cache if needed

#### Phase 3: Application Layer
- Start backend services
- Verify service discovery
- Start frontend services

#### Phase 4: Validation
- Run health checks
- Perform smoke tests
- Monitor metrics