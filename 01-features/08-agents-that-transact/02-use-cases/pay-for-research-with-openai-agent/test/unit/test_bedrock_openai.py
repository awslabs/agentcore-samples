from bedrock_openai import configure_bedrock_openai


def test_configures_bedrock_openai_and_disables_web_search_by_default(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("BEDROCK_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("BEDROCK_OPENAI_WEB_SEARCH_ENABLED", raising=False)
    monkeypatch.setattr(
        "bedrock_openai.provide_token",
        lambda region: f"token-for-{region}",
    )

    runtime = configure_bedrock_openai()

    assert runtime.model == "openai.gpt-5.5"
    assert runtime.region == "us-east-1"
    assert runtime.include_web_search is False
