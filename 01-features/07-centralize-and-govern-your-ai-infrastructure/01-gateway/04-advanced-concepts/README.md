# Advanced Concepts

Advanced gateway patterns including interceptors, security controls, observability, and custom tool behaviors for Amazon Bedrock AgentCore gateway.

## Tutorials

| Section                                                                                                   | Description                                                                                               |
| :-------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------- |
| [fine-grain-access-control](fine-grain-access-control/)                                                   | JWT scope-based fine-grained access control with REQUEST + RESPONSE interceptors                          |
| [prevent-sql-injection](mcp-targets/prevent-sql-injection/)                                               | Detect and block SQL injection in tool inputs with a REQUEST interceptor                                  |
| [sensitive-data-masking](mcp-targets/sensitive-data-masking/)                                             | Mask PII in tool responses using a RESPONSE interceptor + Bedrock Guardrails                              |
| [header-query-propagation](mcp-targets/header-query-propagation/)                                         | Propagate custom HTTP headers and query parameters from clients to targets                                |
| [header-query-propagation/custom-header-query](mcp-targets/header-query-propagation/custom-header-query/) | Allowlisted headers, query params, interceptor precedence rules                                           |
| [header-query-propagation/token-passthrough](mcp-targets/header-query-propagation/token-passthrough/)     | Pass client Authorization token through to targets via interceptor                                        |
| [semantic-search-tool](mcp-targets/semantic-search-tool/)                                                 | Semantic search across 300+ tools for improved agent latency                                              |
| [gateway-observability](gateway-observability/)                                                           | CloudWatch metrics, logs, traces, and CloudTrail auditing                                                 |
| [web-application-firewall](web-application-firewall/)                                                     | Protect the gateway with AWS WAF: associate a regional web ACL, managed + rate-based rules, failure modes |

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [gateway Interceptors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html)
- [Header Propagation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html)
- [Protecting your gateway with AWS WAF](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-waf.html)
