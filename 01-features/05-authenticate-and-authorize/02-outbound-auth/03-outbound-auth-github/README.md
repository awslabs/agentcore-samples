# GitHub delegated consent

Follow the [shared desktop consent workflow](../DELEGATED_CONSENT.md) for the exact provider scope and callback requirements. The caller uses an existing workload and provider; Identity binds the authorization to the requesting user's JWT. No model or chat deployment is needed to test consent.

The [older walkthrough](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/05-authenticate-and-authorize/02-outbound-auth/03-outbound-auth-github) additionally demonstrated private-repository listing and the Cognito chat UI. Those capabilities are not part of the replacement. Reconcile old resources using their original IDs; never remove a shared provider or pool by name.
