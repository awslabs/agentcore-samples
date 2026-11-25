# Call Retrieve without a model

The target binds the knowledge base ID and fixes the result limit at five. Callers supply only the query text.

Create the [single-KB Gateway](../README.md) and set `GATEWAY_URL`, `REGION` and `TARGET_NAME=knowledge` from that stack. From the parent tutorial directory in a fresh Python 3.12 environment:

```bash
python -m pip install -r requirements.txt
python 01-raw-mcp/raw_mcp_call.py 'What does the synthetic warranty document cover?'
```

The program signs the regional Gateway request using your AWS credential chain, initializes the MCP session, verifies the target tool is listed, and calls it with `retrievalQuery.text`. Output can contain document content and source locations: don't redirect it into shared logs. Zero matches is a valid outcome; an MCP tool error is not. See the [stack cleanup instructions](../README.md#query-it); there is no other infrastructure to delete.
