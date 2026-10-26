# Temporal Operators

Dogwood provides four temporal operators. Each one answers a different question about the session history.

Time window units are `s` (seconds), `m` (minutes), `h` (hours), and `d` (days). The maximum window is 24 hours.

---

## `formerly within W <predicate>`

**Question it answers:** "Did this event happen at least once in the last W?"

This is the workhorse operator. It scans the session history within the time window and returns true if it finds at least one event matching the predicate. Most sequencing, freshness, and integrity patterns use it.

```
// Did get_account_balance complete successfully in the last hour?
formerly within 1h AgentCore::Action::"BankingTarget___get_account_balance"::response{
    eventResource: resource
}
```

The window slides with wall-clock time. If the matching event happened 61 minutes ago and the window is `1h`, the condition is false because the event has aged out.

---

## `!L since within W R`

**Question it answers:** "Did anchor event R happen within the last W, and has condition L NOT occurred since?"

This operator tracks a hold: a state that must have started (R) and must not have been broken (L). It is the key to one-time-use approval patterns.

Read it as two parts:

- `since within W R`: anchor event R occurred within the window (same as `formerly`)
- `!L`: condition L has not occurred since that anchor

```
// Was there a get_account_balance (the approval) in the last hour,
// AND has no transfer_funds (the consumption) completed since?
!AgentCore::Action::"BankingTarget___transfer_funds"::response{ eventResource: resource }
since within 1h AgentCore::Action::"BankingTarget___get_account_balance"::response{ eventResource: resource }
```

Walk through what happens in a session:

```
get_account_balance completes  → anchor R is set, !L is true (no transfer yet)  → PERMIT
transfer_funds completes       → L fires, condition breaks
next transfer_funds arrives    → !L is now false                                 → DENY
new get_account_balance runs   → fresh anchor R, !L resets to true              → PERMIT again
```

The important detail: `L` uses `::response`, not `::request`. A transfer counts as "consumed" only after it succeeds. The request being evaluated has not produced a response yet, so it does not block itself.

---

## `count for (t: Timepoint). where (...)`

**Question it answers:** "How many matching events occurred in the window, and does that number exceed a threshold?"

This operator counts events and compares the total against a number. It is used for rate limiting.

```
// Have there been more than 3 transfer_funds requests in the last 5 minutes?
exists (n: Long).
    (count for (t: Timepoint).
        where (formerly within 5m (
            AgentCore::Action::"BankingTarget___transfer_funds"::request{
                eventResource: resource
            } && tp(t)
        ))) == n
    && n > 3
```

The `tp(t)` binding is required boilerplate. It captures the timepoint of each matching event so the engine can count distinct occurrences.

The `exists (n: Long)` binding gives the computed count a name so you can compare it in the `&&` clause that follows.

**Self-referential by default:** the count includes the current request being evaluated. With threshold `> 3`:

```
Call 1 → count = 1, 1 > 3 false → PERMIT
Call 2 → count = 2, 2 > 3 false → PERMIT
Call 3 → count = 3, 3 > 3 false → PERMIT
Call 4 → count = 4, 4 > 3 true  → DENY
```

So `> 3` allows exactly 3 calls and blocks the 4th. If you want to allow N calls, your threshold should be `> N`.

---

## `sum field for (f: Type), (t: Timepoint). where (...)`

**Question it answers:** "What is the running total of a numeric field across matching events, and has it crossed a threshold?"

This operator adds up a numeric input field across all matching events in the window. It is used for cumulative budget caps.

```
// Has the total amount across all transfer_funds requests in the last 24h reached 60000?
exists (total: Long).
    (sum amt for (amt: Long), (t: Timepoint).
        where (formerly within 24h (
            AgentCore::Action::"BankingTarget___transfer_funds"::request{
                eventResource:  resource,
                input.amount:   amt
            } && tp(t)
        ))) == total
    && total >= 60000
```

The line `input.amount: amt` inside the predicate body does two things at once:

1. **Filters:** only events that have an `input.amount` field are considered. Events without it are skipped.
2. **Binds:** for each event that does match, the value of `input.amount` is copied into the variable `amt`. The `sum` operator then adds all those `amt` values together.

Walk through a concrete session:

```
Transfer $15,000 recorded  → input.amount = 15000, bound to amt = 15000
Transfer $22,000 recorded  → input.amount = 22000, bound to amt = 22000
Transfer $30,000 arriving  → input.amount = 30000, bound to amt = 30000

sum of all amt values = 15000 + 22000 + 30000 = 67000

exists (total: Long). 67000 == total && total >= 60000  → true → DENY
```

Like `count`, it is self-referential: the current request's amount is included in the total. The third transfer is not $30,000 over the limit; the running total including that transfer is what triggers the deny.

---

## Composing Operators

Operators can be combined inside a single `when temporal { }` block with `&&`. Both must hold for the block to be true. You can require two prerequisites to have completed in any order by writing two `formerly` conditions joined with `&&`:

```
when temporal {
    formerly within 1h ...get_client_profile::response{ eventResource: resource }
    && formerly within 1h ...load_portfolio::response{ eventResource: resource }
}
```

A policy can contain at most **3 temporal operators** and a maximum nesting depth of **1** (one operator inside another operator's `where` clause, as shown in the `count` and `sum` examples above). Split more complex logic across multiple policies. See [Limits and Constraints](./limits.md).

---

Previous: [Predicate Anatomy and Event Recording](./predicates.md) | Next: [Common Use Cases](./patterns.md)
