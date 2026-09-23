# Troubleshooting Guides

This document contains detailed troubleshooting guides for common operational issues.

## Pod CrashLoopBackOff Troubleshooting

**Guide ID:** `pod-crashloop-troubleshooting`  
**Category:** Kubernetes

### Troubleshooting Steps
1. Get pod details: `kubectl describe pod <pod-name>`
2. Check pod logs: `kubectl logs <pod-name> --previous`
3. Look for recent events: `kubectl get events --sort-by=.metadata.creationTimestamp`
4. Verify resource limits and requests
5. Check liveness and readiness probes
6. Review container image and configuration
7. Validate environment variables and secrets

### Common Causes
- Insufficient resources (CPU/Memory)
- Incorrect liveness probe configuration
- Missing environment variables
- Invalid container image or tag
- Configuration errors in deployment

### Diagnostic Commands
- `kubectl get pod <pod-name> -o yaml`
- `kubectl logs <pod-name> --previous`
- `kubectl describe pod <pod-name>`
- `kubectl get events --field-selector involvedObject.name=<pod-name>`

---

## Database Pod CrashLoopBackOff Resolution

**Guide ID:** `database-crashloop-troubleshooting`  
**Category:** Kubernetes  
**Specific Pod:** `database-pod-7b9c4d8f2a-x5m1q`

### Troubleshooting Steps
1. Check pod logs: `kubectl logs database-pod-7b9c4d8f2a-x5m1q --previous`
2. Verify ConfigMap exists: `kubectl get configmap database-config -n production`
3. Check volume mounts: `kubectl describe pod database-pod-7b9c4d8f2a-x5m1q`
4. Verify PVC status: `kubectl get pvc -n production`
5. Check permissions on data directory
6. Create missing ConfigMap if needed: `kubectl create configmap database-config --from-file=database.conf`
7. Fix volume permissions: `chmod 700 /var/lib/postgresql/data && chown postgres:postgres /var/lib/postgresql/data`

### Common Causes
- Missing ConfigMap 'database-config'
- Incorrect file permissions on data directory
- Volume mount failures
- Missing database configuration file
- PostgreSQL initialization failures

### Diagnostic Commands
- `kubectl logs database-pod-7b9c4d8f2a-x5m1q -c postgres --previous`
- `kubectl describe pod database-pod-7b9c4d8f2a-x5m1q`
- `kubectl get configmap -n production | grep database`
- `kubectl get pvc -n production`
- `kubectl get events --field-selector involvedObject.name=database-pod-7b9c4d8f2a-x5m1q --sort-by='.lastTimestamp'`

### Resolution

#### Immediate Fix
```bash
kubectl create configmap database-config \
  --from-literal=database.conf='shared_buffers=256MB\nmax_connections=100' \
  -n production
```

#### Permanent Fix
Update deployment manifest to include proper ConfigMap and volume permissions

**Impact:** Critical - Complete database outage affecting all services  
**Estimated Resolution Time:** 10-15 minutes

---

## High Response Time Investigation

**Guide ID:** `high-response-time-troubleshooting`  
**Category:** Performance

### Investigation Steps
1. Check current response time metrics
2. Identify affected endpoints and services
3. Review CPU and memory utilization
4. Examine database query performance
5. Check for network latency issues
6. Review application logs for bottlenecks
7. Verify external service dependencies

### Tools
- `kubectl top pods`
- Application Performance Monitoring (APM)
- Database query analysis tools
- Network monitoring tools

### Common Causes
- Database query optimization needed
- Insufficient service resources
- Network latency or packet loss
- External service degradation
- Cache misses or invalidation

---

## Memory Leak Investigation Guide

**Guide ID:** `memory-leak-investigation`  
**Category:** Performance

### Investigation Steps
1. Monitor memory usage trends over time
2. Identify services with increasing memory usage
3. Capture heap dumps if possible
4. Review recent code changes
5. Check for unclosed resources
6. Analyze object allocation patterns
7. Test fixes in staging environment

### Diagnostic Commands
- `kubectl top pods --containers`
- `kubectl exec <pod> -- jmap -heap <pid>`
- `kubectl exec <pod> -- jstat -gcutil <pid>`

### Prevention Measures
- Implement proper resource cleanup
- Use connection pooling
- Set appropriate JVM heap settings
- Monitor memory metrics continuously

---

## Service Discovery Troubleshooting

**Guide ID:** `service-discovery-issues`  
**Category:** Networking

### Troubleshooting Steps
1. Verify service endpoints: `kubectl get endpoints`
2. Check service selector labels
3. Test DNS resolution from pods
4. Verify network policies
5. Check service port configurations
6. Test connectivity between pods
7. Review ingress configurations

### Diagnostic Commands
- `kubectl get svc <service-name> -o yaml`
- `kubectl get endpoints <service-name>`
- `kubectl exec <pod> -- nslookup <service-name>`
- `kubectl exec <pod> -- curl <service-name>:<port>`

### Common Issues
- Mismatched selector labels
- Incorrect port configurations
- Network policy restrictions
- DNS configuration issues