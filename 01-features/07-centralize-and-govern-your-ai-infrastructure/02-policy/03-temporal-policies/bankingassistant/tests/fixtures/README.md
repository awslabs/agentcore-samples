# Negative service-model fixture

`legacy-control-model.json.gz` contains the unmodified retained old AgentCore
service-model JSON used by the operator/reviewer reproduction, compressed with
a deterministic gzip header. It is SDK schema data only, not credentials,
configuration, deployment state or a Runtime build artifact.

Uncompressed SHA256:
`3134fd3d05d47f1c3b3ec5013568d47a01e15c10df8c39e25ec0201b31d844f6`

Tests place these same bytes in private `2023-06-05` and **synthetic**
`2099-01-01` directories. No published future API is claimed. Positive requests
and responses use the genuine hash-locked published botocore 1.44.9 model;
this fixture is used only to prove refusal before clients/intent writes.
