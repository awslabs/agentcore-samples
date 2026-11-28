# Historical sample: Visa B2B accounts payable

This procure-to-pay workshop is archived, not a supported deployment path. It demonstrated invoice and goods-receipt matching, simulated card workflows, and ISO 20022 file generation. The [historical source and diagrams](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/02-use-cases/02-workflow-automation-agents/visa-b2b-account-payable-agent) are available for reference, not as deployment instructions.

The historical checkout lacks six CDK stack modules imported by its entry points. Its deployment scripts also selected IAM roles and a Lambda by name and could update an existing Runtime on conflict. Replacing only the launcher would not produce a reproducible deployment with verified resource ownership.

For other payment scenarios, see [Agents that transact](../../../01-features/08-agents-that-transact/). Those samples are **not** replacements for Visa B2B integration, invoice reconciliation, or ISO 20022 output.

**Existing deployments:** removing the source does not remove resources or stop charges. Inventory resources using your original CloudFormation stack IDs and deployment records, including resources created outside the stacks. Review retention and dependencies for databases, buckets, keys, and logs before cleanup. Do not run historical scripts against same-name resources or delete shared roles. Reconcile payment-provider credentials separately with their owner.
