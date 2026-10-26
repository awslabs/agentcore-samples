---
name: pto-planning
description: Check an employee's PTO balance and policy, then plan or submit a time-off request
allowed-tools:
  - get_pto_balance
  - lookup_hr_policy
  - submit_pto_request
---

# PTO Planning Instructions

Use this skill when an employee asks to check, plan, or submit paid time off.

## Required workflow

1. Identify the employee ID and whether the request is informational or asks for a submission.
2. Call `get_pto_balance` for the employee before making any recommendation.
3. Call `lookup_hr_policy` with `topic="pto"` and explain the relevant notice and rollover rules.
4. If the user explicitly asks to submit time off and provides both dates:
   - Confirm that the balance is available.
   - Call `submit_pto_request` with the employee ID, start date, end date, and the user's reason when provided.
5. If required information is missing, ask for it instead of inventing values or submitting a request.
6. Return a concise **PTO Planning Summary** containing:
   - Employee ID
   - Remaining PTO balance
   - Relevant policy requirements
   - Requested dates, when supplied
   - Submission status and request ID, when submitted

Never claim that a request was submitted unless `submit_pto_request` returned a successful result.
