# receiptsagent

The AgentCore Runtime application for the Receipts IDP sample. Phase 1 is a stub
entrypoint (`main.py`) that proves the Runtime deploys and is invokable. The OCR
+ dual-agent extraction pipeline lands in later phases — see the repo
`IMPLEMENTATION-PLAN.md`.

`config.py` is the single place env vars are read (the replaceable deploy seam).
