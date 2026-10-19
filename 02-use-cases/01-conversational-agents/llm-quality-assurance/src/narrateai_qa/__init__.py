"""NarrateAI quality assurance samples for production LLM applications.

Three techniques from the NarrateAI blog post, each usable independently:

- narrateai_qa.streaming_evaluation: real-time paragraph-level evaluation of
  streamed LLM output using a producer-consumer pipeline (stdlib only).
- narrateai_qa.adaptive_pipeline: volume-based query routing with greedy
  first-fit packing (fast path vs. normal path).
- narrateai_qa.multi_model_failover: a Strands BedrockModel provider that
  fails over across model rankings and AWS account quota spaces.
"""

__version__ = "1.0.0"
