import pytest
from pay_for_research import build_agent, build_agent_team, build_prompt


class FakePaymentClient:
    def fetch(self, url: str) -> str:
        return f'{{"ok": true, "url": "{url}"}}'

    def session_status(self) -> str:
        return '{"available_spend": "0.20"}'


def test_builds_manager_with_two_bounded_specialists() -> None:
    team = build_agent_team(
        FakePaymentClient(),
        approved_paid_url="https://merchant.example/research",
        model="gpt-5.6-sol",
    )

    assert team.lead.model == "gpt-5.6-sol"
    assert team.public_evidence.name == "Public evidence analyst"
    assert team.premium_evidence is not None
    assert team.premium_evidence.name == "Premium evidence analyst"
    assert [tool.name for tool in team.lead.tools] == [
        "research_public_evidence",
        "research_premium_evidence",
    ]
    assert [tool.name for tool in team.premium_evidence.tools] == [
        "fetch_approved_premium_source",
        "payment_session_status",
    ]
    assert team.public_evidence.tools[0].name == "web_search"


def test_payment_authority_exists_only_on_premium_specialist() -> None:
    team = build_agent_team(
        FakePaymentClient(),
        approved_paid_url="https://merchant.example/research",
        model="openai.gpt-5.5",
        include_web_search=False,
    )

    assert team.public_evidence.tools == []
    assert team.premium_evidence is not None
    assert [tool.name for tool in team.premium_evidence.tools] == [
        "fetch_approved_premium_source",
        "payment_session_status",
    ]
    assert all(tool.name != "fetch_approved_premium_source" for tool in team.lead.tools)


def test_omits_entire_premium_specialist_without_approved_url() -> None:
    team = build_agent_team(None, model="gpt-5.6-sol")

    assert team.premium_evidence is None
    assert [tool.name for tool in team.lead.tools] == ["research_public_evidence"]


def test_requires_payment_client_for_approved_source() -> None:
    with pytest.raises(ValueError, match="payment_client"):
        build_agent_team(
            None,
            approved_paid_url="https://merchant.example/research",
        )


def test_build_agent_remains_compatibility_wrapper() -> None:
    agent = build_agent(None, model="gpt-5.6-sol")

    assert agent.name == "Financial research lead"
    assert [tool.name for tool in agent.tools] == ["research_public_evidence"]


def test_prompt_disables_paid_tool_without_an_approved_url() -> None:
    prompt = build_prompt("Research AMZN", None)

    assert "premium specialist is unavailable" in prompt
