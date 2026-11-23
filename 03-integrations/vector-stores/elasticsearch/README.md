# Search Elasticsearch policy documents through AgentCore Gateway

Give an MCP consumer one read-only search tool. An authenticated Gateway routes `policies___search` to Lambda. Lambda queries `attachment.content` in **one configured Elasticsearch index**, returning up to three filenames and short excerpts. This is a `multi_match` **full-text** query, not vector or hybrid retrieval. Results are untrusted document excerpts; don't treat them as instructions or authorization to run another tool.

## Prepare a sandbox

- Bring an HTTPS Elasticsearch origin you own and trust, a dedicated sandbox index, and an API key restricted to reading that index. Give its owner the [sample policy documents](policies/) to index with fields `filename` and `attachment.content`. This tutorial does **not** bulk-write documents or change index settings. For example, one document can have `{"filename":"warranty.txt","attachment":{"content":"Demo warranty covers repairs for one year."}}`.
- Store only the encoded API key in an existing Secrets Manager `SecretString` in the same account and Region (AWS-managed Secrets Manager key for this example). Supply its full ARN, never the key, to the template. Verify the origin carefully: it is where the key will be sent. Lambda verifies TLS and does not follow redirects. A publicly reachable Elasticsearch endpoint with a trusted certificate is required; private network setup is not included.
- Bring a Cognito pool, app client and dedicated search scope in the same Region. Use short-lived access tokens from the existing authorized client flow. Configure your MCP client via its secure credential mechanism. Do not paste tokens into notebook cells, URLs or CLI arguments. This example grants search across the configured index, **not per-document tenant isolation**.
- Use temporary AWS credentials, a fresh stack/Gateway name and an existing **versioned** artifact bucket. Gateway, Lambda, logging and Elasticsearch can incur charges. Review `template.yaml`: Gateway can invoke only this function; Lambda can read only the specified secret and write to its own log group. No service role may edit the index or create additional Gateways.

## Publish the target

From this directory set `AWS_REGION`, `STACK`, `GATEWAY_NAME`, `BUCKET`, `ELASTIC_ORIGIN`, `INDEX`, `SECRET_ARN`, `POOL`, `CLIENT` and `SCOPE` to values approved by the owners. Use commercial AWS. Verify the account before creating resources.

```bash
aws sts get-caller-identity
artifact_dir=$(mktemp -d)
zip -j "$artifact_dir/handler.zip" handler.py
KEY="elastic-policy-search/$(python3 -c 'import uuid; print(uuid.uuid4())').zip"
VERSION=$(aws s3api put-object --region "$AWS_REGION" --bucket "$BUCKET" --key "$KEY" \
  --body "$artifact_dir/handler.zip" --if-none-match '*' --query VersionId --output text)
[[ -n "$VERSION" && "$VERSION" != None && "$VERSION" != null ]] || { echo 'Versioned bucket required'; exit 1; }
STACK_ID=$(aws cloudformation create-stack --region "$AWS_REGION" --stack-name "$STACK" --template-body file://template.yaml \
  --capabilities CAPABILITY_IAM --parameters ParameterKey=ArtifactBucket,ParameterValue="$BUCKET" \
  ParameterKey=ArtifactKey,ParameterValue="$KEY" ParameterKey=ArtifactVersion,ParameterValue="$VERSION" \
  ParameterKey=GatewayName,ParameterValue="$GATEWAY_NAME" ParameterKey=ElasticOrigin,ParameterValue="$ELASTIC_ORIGIN" \
  ParameterKey=ElasticIndex,ParameterValue="$INDEX" ParameterKey=KeySecretArn,ParameterValue="$SECRET_ARN" \
  ParameterKey=UserPoolId,ParameterValue="$POOL" ParameterKey=AppClientId,ParameterValue="$CLIENT" \
  ParameterKey=SearchScope,ParameterValue="$SCOPE" --query StackId --output text)
aws cloudformation wait stack-create-complete --region "$AWS_REGION" --stack-name "$STACK_ID"
aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name "$STACK_ID" \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayUrl`].OutputValue' --output text
```

Keep the stack ID and exact artifact bucket, key and version. Use your authorized MCP client to list tools on the returned Gateway URL, then call `policies___search` with `{"query":"warranty repairs"}`. A miss returns `{"matches":[]}`. The consumer cannot choose the destination, index, projection or API key. Reference documents may contain sensitive material; use synthetic data only.

## Cleanup

Verify the account/Region and full stack ID. Delete **only** that stack and wait for `stack-delete-complete`. Then remove **only** the object version you uploaded (`aws s3api delete-object --bucket "$BUCKET" --key "$KEY" --version-id "$VERSION" --region "$AWS_REGION"`). The index, secret, pool, app client and artifact bucket remain externally owned. Do not enumerate and delete all Gateways, secrets or indexes in an account.

The [historical notebook](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/vector-stores/elasticsearch) included indexing, Cognito creation, a model-backed Strands consumer and a second Runtime deployment. This example replaces the Gateway target, not that provisioning pipeline. For existing deployments, verify original IDs and ownership before cleanup. [Other Gateway targets](../../../01-features/07-centralize-and-govern-your-ai-infrastructure/01-gateway/01-attach-targets/) cover alternatives.
