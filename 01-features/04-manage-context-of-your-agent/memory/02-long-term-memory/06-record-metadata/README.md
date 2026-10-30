# Record metadata

Memory records can carry structured metadata (key → value) so retrieval can apply hard filters at the index instead of in the prompt. Typical uses: region, tier, source system, language, retention class.

## What you learn

- Attach metadata when writing via `BatchCreateMemoryRecords` (each record's `metadata` field is a `string → MetadataValue` map)
- Declare `indexedKeys` on `CreateMemory` so the keys you intend to filter on are pre-indexed
- Filter retrieval via `searchCriteria.metadataFilters` (`EQUALS_TO`, `EXISTS`, `NOT_EXISTS`)

## Run

```bash
pip install -r ../requirements.txt   # indexedKeys needs boto3>=1.43.35
python structured-metadata.py boto3   # default — direct service calls
python structured-metadata.py sdk     # documents the SDK gap (no indexedKeys / batch / metadataFilters helpers)
```

## Best practices

- **Pre-declare `indexedKeys`** on the memory resource at creation. Once declared they cannot be removed, and they are required for index-side filtering.
- **Keep keys low-cardinality and stable** (`region`, `tier`, `source`). Don't put free-form values here.
- **Don't store secrets in metadata.** Same reasoning as event metadata — it isn't encrypted with your CMK.
- **Prefer metadata over namespace explosion.** If a record can be split *or* filtered by an attribute, prefer filtering — a smaller namespace tree is easier to scope with IAM and easier to evolve.
- **Combine with namespace.** Namespace = ownership/scope; metadata = orthogonal attributes. They compose well.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create memory and declare indexedKeys (cannot be removed later).
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "RecordMetaCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --indexed-keys '[
    {"key":"region","type":"STRING"},
    {"key":"tier","type":"STRING"}
  ]' \
  --query 'memory.id' --output text)


# Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
# and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. Batch-create records with metadata
aws bedrock-agentcore batch-create-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --records '[
    {
      "requestIdentifier":"rec-eu-premium",
      "content":{"text":"Acme prefers GDPR-compliant data residency."},
      "namespaces":["/tenants/tenant-acme/notes/"],
      "timestamp":"'"$(date +%s)"'",
      "metadata":{"region":{"stringValue":"EU"},"tier":{"stringValue":"premium"}}
    }
  ]'

# 3. Retrieve filtered to region=EU
aws bedrock-agentcore retrieve-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace "/tenants/tenant-acme/notes/" \
  --search-criteria '{
    "searchQuery":"Acme",
    "topK":10,
    "metadataFilters":[{
      "left":{"metadataKey":"region"},
      "operator":"EQUALS_TO",
      "right":{"metadataValue":{"stringValue":"EU"}}
    }]
  }'

# 4. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
