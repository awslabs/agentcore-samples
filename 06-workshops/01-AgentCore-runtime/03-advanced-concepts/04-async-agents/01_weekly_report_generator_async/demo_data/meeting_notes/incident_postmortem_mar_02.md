# Incident Postmortem - Production Outage Jan 24, 2026

**Incident ID:** INC-2026-0124-001  
**Duration:** 47 minutes (2:15 PM - 3:02 PM PST)  
**Severity:** High  
**Impact:** 100% of users unable to authenticate

## Timeline
- **2:15 PM** - Monitoring alerts triggered for auth service failures
- **2:18 PM** - On-call engineer (Samantha Brooks) acknowledged alert
- **2:22 PM** - Identified memory leak in auth service causing OOM errors
- **2:35 PM** - Decision made to restart affected pods
- **2:41 PM** - Services restarted, authentication partially restored
- **2:58 PM** - All services healthy, monitoring normal
- **3:02 PM** - Incident declared resolved

## Root Cause
Memory leak in authentication service v2.4.1 caused by improper cleanup of session objects. Under high load, memory usage grew until OOM killer terminated processes.

## Resolution
- Immediate: Restarted affected services
- Short-term: Rolled back to v2.4.0 (stable version)
- Long-term: Fixed memory leak in code, added memory profiling to CI/CD

## Action Items
- [x] Samantha: Implement memory usage alerts (completed Jan 25)
- [ ] Alex: Add memory leak detection to automated tests (due Jan 31)
- [ ] DevOps: Improve pod restart automation (due Feb 5)
- [ ] Maya: Send customer communication about incident (completed Jan 24)

## Lessons Learned
- Need better memory profiling in staging environment
- Monitoring alerts worked well - detected within 3 minutes
- Incident response time was excellent (8 minutes to diagnosis)
- Should have caught this in load testing
