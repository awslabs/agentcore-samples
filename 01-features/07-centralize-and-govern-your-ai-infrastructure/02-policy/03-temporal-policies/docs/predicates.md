# Predicate Anatomy and Event Recording

A **predicate** identifies which past events a temporal condition should match. Understanding predicates first makes event recording easier to reason about, because a predicate is the thing you write inside every temporal operator.

## Predicate Anatomy

A predicate has three parts written left to right:

```
AgentCore::Action::"TargetName___toolFunctionName"::eventkind{ field: value, ... }
        ▲                       ▲                       ▲              ▲
  namespace prefix        action identifier          event kind    field constraints
```

### Part 1: Action Identifier

The action identifier names the specific tool call you want to match. It is always in `TargetName___toolFunctionName` format (three underscores between the gateway target name and the tool's function name).

For example, if your gateway target is named `BankingTarget` and the tool function is `get_account_balance`, the full action identifier is:

```
AgentCore::Action::"BankingTarget___get_account_balance"
```

If you rename the target or the function in your gateway configuration, you must update the policy to match. The string is an exact match.

### Part 2: Event Kind

Appended with `::`, one of `::request`, `::response`, or `::error`. This tells the predicate which stage of the tool call lifecycle to look at.

```
AgentCore::Action::"BankingTarget___get_account_balance"::response
```

The full event recording section below explains exactly when each kind is written and what fields it carries.

### Part 3: Field Constraints Body

A `{ }` block containing field name/value pairs that the matched event must satisfy. An event only matches the predicate if every constraint in the body holds.

There are four field categories you can constrain:

| Field | Available on | Description |
|---|---|---|
| `input.<name>` | `::request`, `::response`, `::error` | An input argument the action received |
| `output.<name>` | `::response` only | A value the tool returned |
| `eventResource` | all kinds | The gateway resource the event was recorded against |
| `eventPrincipal` | all kinds | The principal that made the recorded request |

## Two Rules That Apply to Every Predicate

**Rule 1:** `eventResource: resource` is required in every predicate body. It pins the historical lookup to the same gateway resource as the current request, ensuring that session history from a different gateway cannot bleed into the evaluation. A policy without it will fail to create.

**Rule 2:** Every field constraint in a predicate body does two things at once:

1. **Filters:** only events where the field matches the right-hand side are considered. Events that do not match are skipped.
2. **Binds / resolves:** the right-hand side determines what value is used for the comparison.

You have three choices for the right-hand side:

| Right-hand side | What it does |
|---|---|
| A hardcoded value e.g. `"ACC-8821"` | Filters to events where the field was exactly that value. Fixed at authoring time, does not depend on the current request. |
| `context.input.<name>` | Resolves to whatever the current request is sending in that input field. Evaluated fresh on every request. |
| A declared variable e.g. `amt` | Copies the field's value from each matching event into the variable, for use in `sum` or `count` aggregations. |

**Hardcoded value:**

```
output.accountId: "ACC-8821"    // only matches events where the tool returned exactly ACC-8821
```

Almost never useful for integrity checks, because you would have to know the account ID at policy-authoring time.

**`context.input.<name>`:**

```
output.accountId: context.input.toAccount
```

Filters to events where `output.accountId` equals whatever `toAccount` the current `transfer_funds` call is sending right now. The value resolves fresh on every evaluation against the live request.

**Declared variable (used with `sum` and `count`):**

```
input.amount: amt
```

Filters to events that have an `input.amount` field, and for each one that matches, copies its value into `amt` so the `sum` operator can add them up.

### Walk-through: Output-to-Input Integrity

**Case 1: Agent uses the real account ID**
```
get_account_balance runs → returns output.accountId = "ACC-8821"
transfer_funds arrives   → sending input.toAccount  = "ACC-8821"

Predicate checks: output.accountId == context.input.toAccount
                         "ACC-8821"  ==     "ACC-8821"          ✓ match found → PERMIT
```

**Case 2: Agent substitutes a fabricated ID**
```
get_account_balance runs → returns output.accountId = "ACC-8821"
transfer_funds arrives   → sending input.toAccount  = "ACC-3347"  (fabricated)

Predicate checks: output.accountId == context.input.toAccount
                         "ACC-8821"  ==     "ACC-3347"          ✗ no match → DENY
```

The policy does not know `"ACC-8821"` at authoring time. It simply requires that whatever the current transfer is trying to use must equal something the system actually returned earlier in the session. That is why the pattern is called output-to-input integrity: the prior tool's output is pinned to the current tool's input.

### Complete Predicate Example

The Scenario 1 policy requires that `transfer_funds` can only proceed if a prior `get_account_balance` response returned an `accountId` that matches what `transfer_funds` is now trying to use as its `toAccount` argument:

```
AgentCore::Action::"BankingTarget___get_account_balance"::response{
    eventResource:    resource,                   // required: same gateway
    output.accountId: context.input.toAccount     // historical output must equal current input
}
```

Reading it in plain English: "find a `::response` event from `get_account_balance` on this gateway where the `accountId` the tool returned equals the `toAccount` the current `transfer_funds` request is sending."

---

## Event Recording

Every action that passes through AgentCore Gateway produces events that the policy engine records. Think of the three event kinds as a timeline for a single tool call:

```
User/Agent sends request
        │
        ▼
  Gateway receives it
        │
        ├── Policy engine evaluates ──► DENIED?
        │                                   │
        │                                   └── records ::error  (input fields only)
        │                                        stops here, tool never called
        │
        ├── PERMITTED ──► ::request recorded  (input fields)
        │                      │
        │                      ▼
        │             Tool / Lambda runs
        │                      │
        │             ├── Tool returns error ──► ::error recorded  (input fields only)
        │             │
        │             └── Tool succeeds ──────► ::response recorded  (input + output fields)
        │
        ▼
  Response returned to caller
```

Each event belongs to one of three kinds:

| Event kind | When it is recorded | Fields it carries |
|---|---|---|
| `::request` | Once the policy engine permits the request, before the tool runs | Input fields only |
| `::response` | After the tool returns successfully | Input fields and output fields |
| `::error` | When the policy engine denies the request, or the tool itself returns an error | Input fields only |

### What Each Kind Means in Practice

**`::request`** is recorded the moment the policy says "yes, proceed." The tool has not run yet, so there are no output fields. Use this when you only care that an action was *attempted*, not whether it succeeded. Rate limiting uses `::request` because you want to count every dispatched call, not just the ones that completed cleanly.

**`::response`** is recorded only after the tool runs and returns successfully. This is the only event kind that carries output fields: what the tool actually returned. The output-to-input integrity policy in Scenario 1 depends on this:

```
permit (...transfer_funds...)
when temporal {
    formerly within 1h ...get_account_balance::response{
        eventResource:    resource,
        output.accountId: context.input.toAccount   // output field only exists on ::response
    }
};
```

The field `output.accountId` only exists on a `::response` event. If `get_account_balance` was denied or errored, `::response` was never written, the condition finds nothing, and `transfer_funds` is denied. A fabricated or attacker-substituted account ID cannot match a value the system never actually returned.

**`::error`** covers two distinct situations that share one event kind:

1. The policy engine denied the request. The tool was never called.
2. The policy engine permitted the request, the tool ran, and the tool itself returned an error.

Both land in `::error`. The "block after prior denial" pattern uses this: if `get_account_balance` was denied (a suspicious signal), you forbid the subsequent `transfer_funds` by checking for `get_account_balance::error` in the session history.

### The Dependency Trap

Because `::request` is only written after a PERMIT decision, every action you want to appear in session history needs its own permit. This applies to all three event kinds, not just `::response`.

Without a plain permit for `get_account_balance`:

```
get_account_balance arrives → no permit → DENY → ::error written, ::request never written
transfer_funds arrives → looks for get_account_balance::response → finds nothing → DENY
```

With a plain permit for `get_account_balance` in place:

```
get_account_balance arrives → PERMIT → ::request written → tool runs → ::response written
transfer_funds arrives → looks for get_account_balance::response → finds it → evaluated normally
```

The one exception is `::error` from a policy denial: that is written even without a permit, because it is recording the denial itself. The "block after prior denial" pattern works precisely because the action has no permit and is being denied.

### Timing

A `::response` event is written after the tool returns, so there can be a brief delay before it is visible to subsequent evaluations. This is not a design issue but a runtime one: it only matters if you are programmatically issuing calls back-to-back in a tight loop. The natural latency of an agent's next LLM reasoning step is more than enough of a gap; a human-paced session will never encounter it.

---

Previous: [Policy Sessions](./sessions.md) | Next: [Temporal Operators](./operators.md)
