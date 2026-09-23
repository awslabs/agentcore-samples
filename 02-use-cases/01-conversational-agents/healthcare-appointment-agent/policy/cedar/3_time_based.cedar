// Use Case 3: Time-Based Access — Clinic Hours
//
// Restricts the getSlots tool to clinic hours (9 AM – 9 PM UTC).
//
// Uses context.system.now — a system-provided timestamp injected by the
// gateway at request time. The agent cannot manipulate this value.
//
// Cedar's toTime() extracts the time-of-day component, and duration()
// creates a time value for comparison. The policy reads as:
//   "Allow getSlots only when the current time is >= 9:00 and <= 21:00 UTC"
//
// This matches the blog's natural language prompt:
//   "Allow users to get slots only between 9am and 9pm UTC"
//
// Replace {gateway_arn} with your actual Gateway ARN.

permit(
  principal,
  action == AgentCore::Action::"Target1___getSlots",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  (!(((context.system.now).toTime()) < (duration("9h")))) &&
  ((((context.system.now).toTime()) <= (duration("21h"))))
};
