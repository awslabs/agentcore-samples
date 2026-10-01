# receiptsagent

The AgentCore Runtime application for the Receipts IDP sample. `main.py` is the entrypoint for both
Runtimes: the receipt pipeline (Textract OCR, then the extractor and the independent validator, then a
Cedar-gated save or human review) and the read-only chat assistant.

`config.py` is the single place environment variables are read. How it all fits together is in
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md).
