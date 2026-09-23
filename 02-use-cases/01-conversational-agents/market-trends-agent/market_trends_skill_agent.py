"""Native Strands AgentSkills runtime for deterministic market analysis."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, AgentSkills, tool
from strands.models import BedrockModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

_AGENT_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _AGENT_DIR / "skills"
_EXPECTED_SKILLS = (
    "trend-analysis",
    "sector-rotation",
    "earnings-snapshot",
    "portfolio-risk",
)


def _validate_skill_files() -> None:
    """Fail fast when a required local AgentSkills definition is missing."""
    missing_files = [
        _SKILLS_DIR / skill_name / "SKILL.md"
        for skill_name in _EXPECTED_SKILLS
        if not (_SKILLS_DIR / skill_name / "SKILL.md").is_file()
    ]
    if missing_files:
        missing = ", ".join(str(path) for path in missing_files)
        raise FileNotFoundError(f"Missing required AgentSkills files: {missing}")


_validate_skill_files()

_SKILLS_PLUGIN = AgentSkills(skills=[str(_SKILLS_DIR)])
_MODEL = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

# Deterministic MarketTrends mock data.
_STOCKS: dict[str, dict[str, Any]] = {
    "AAPL": {
        "name": "Apple Inc.",
        "price": 175.43,
        "change": +1.24,
        "change_pct": +0.71,
        "sector": "Technology",
        "market_cap": "2.7T",
        "pe_ratio": 28.4,
        "dividend_yield": 0.52,
    },
    "MSFT": {
        "name": "Microsoft Corp.",
        "price": 415.28,
        "change": -2.15,
        "change_pct": -0.52,
        "sector": "Technology",
        "market_cap": "3.1T",
        "pe_ratio": 35.2,
        "dividend_yield": 0.71,
    },
    "GOOGL": {
        "name": "Alphabet Inc.",
        "price": 164.32,
        "change": +3.41,
        "change_pct": +2.12,
        "sector": "Technology",
        "market_cap": "2.0T",
        "pe_ratio": 24.1,
        "dividend_yield": 0.0,
    },
    "AMZN": {
        "name": "Amazon.com Inc.",
        "price": 198.67,
        "change": +5.23,
        "change_pct": +2.70,
        "sector": "Consumer Discretionary",
        "market_cap": "2.1T",
        "pe_ratio": 54.3,
        "dividend_yield": 0.0,
    },
    "TSLA": {
        "name": "Tesla Inc.",
        "price": 242.84,
        "change": -8.42,
        "change_pct": -3.35,
        "sector": "Consumer Discretionary",
        "market_cap": "0.77T",
        "pe_ratio": 62.1,
        "dividend_yield": 0.0,
    },
    "NVDA": {
        "name": "NVIDIA Corp.",
        "price": 875.20,
        "change": +23.45,
        "change_pct": +2.76,
        "sector": "Technology",
        "market_cap": "2.2T",
        "pe_ratio": 68.4,
        "dividend_yield": 0.03,
    },
    "META": {
        "name": "Meta Platforms Inc.",
        "price": 512.63,
        "change": +7.84,
        "change_pct": +1.55,
        "sector": "Technology",
        "market_cap": "1.3T",
        "pe_ratio": 26.8,
        "dividend_yield": 0.39,
    },
    "JPM": {
        "name": "JPMorgan Chase & Co.",
        "price": 198.43,
        "change": -1.32,
        "change_pct": -0.66,
        "sector": "Financials",
        "market_cap": "0.57T",
        "pe_ratio": 11.8,
        "dividend_yield": 2.31,
    },
    "GS": {
        "name": "Goldman Sachs Group",
        "price": 468.32,
        "change": +8.15,
        "change_pct": +1.77,
        "sector": "Financials",
        "market_cap": "0.15T",
        "pe_ratio": 13.2,
        "dividend_yield": 2.07,
    },
    "JNJ": {
        "name": "Johnson & Johnson",
        "price": 152.84,
        "change": +0.43,
        "change_pct": +0.28,
        "sector": "Healthcare",
        "market_cap": "0.37T",
        "pe_ratio": 15.2,
        "dividend_yield": 3.14,
    },
    "LLY": {
        "name": "Eli Lilly & Co.",
        "price": 782.45,
        "change": +15.32,
        "change_pct": +2.00,
        "sector": "Healthcare",
        "market_cap": "0.74T",
        "pe_ratio": 58.3,
        "dividend_yield": 0.62,
    },
    "XOM": {
        "name": "Exxon Mobil Corp.",
        "price": 113.65,
        "change": -2.10,
        "change_pct": -1.82,
        "sector": "Energy",
        "market_cap": "0.45T",
        "pe_ratio": 13.7,
        "dividend_yield": 3.42,
    },
    "CVX": {
        "name": "Chevron Corp.",
        "price": 156.43,
        "change": -1.85,
        "change_pct": -1.17,
        "sector": "Energy",
        "market_cap": "0.29T",
        "pe_ratio": 14.2,
        "dividend_yield": 4.15,
    },
    "KO": {
        "name": "Coca-Cola Co.",
        "price": 62.34,
        "change": +0.21,
        "change_pct": +0.34,
        "sector": "Consumer Staples",
        "market_cap": "0.27T",
        "pe_ratio": 24.1,
        "dividend_yield": 3.16,
    },
    "PG": {
        "name": "Procter & Gamble",
        "price": 158.72,
        "change": +0.84,
        "change_pct": +0.53,
        "sector": "Consumer Staples",
        "market_cap": "0.37T",
        "pe_ratio": 26.4,
        "dividend_yield": 2.38,
    },
    "SPY": {
        "name": "SPDR S&P 500 ETF",
        "price": 512.34,
        "change": +2.15,
        "change_pct": +0.42,
        "sector": "ETF",
        "market_cap": "N/A",
        "pe_ratio": 22.1,
        "dividend_yield": 1.32,
    },
}

_CONSUMER_DISCRETIONARY_NEWS = [
    "Amazon Prime membership +8% YoY; AWS cloud revenue accelerates to +35% growth",
    "Tesla Q1 delivery miss widens to 13%; management cites Austin production ramp issues",
    "Consumer confidence index hits 18-month high on cooling CPI data",
]
_CONSUMER_STAPLES_NEWS = [
    ("Coca-Cola maintains full-year guidance as global pricing and volume growth offset currency pressure"),
    ("Procter & Gamble margins expand as input-cost inflation eases across household product categories"),
    ("Consumer staples companies report resilient demand while shoppers continue selective trade-down"),
]
_NEWS: dict[str, list[str]] = {
    "technology": [
        "NVIDIA Q1 earnings beat estimates by 15%; raises full-year guidance on AI demand surge",
        "Apple Vision Pro 2 pre-orders open amid strong enterprise adoption signals",
        "Microsoft Azure AI revenue grows 42% YoY; Copilot enterprise seats reach 1.2M",
        "Alphabet launches Gemini Ultra 2.0 with multimodal capabilities, challenging OpenAI",
        "Meta Llama 4 open-weights model outperforms proprietary models on 8 of 10 benchmarks",
    ],
    "healthcare": [
        "Eli Lilly GLP-1 drug Mounjaro sees 78% revenue growth; supply constraints ease in Q2",
        "Johnson & Johnson oncology pipeline Phase 3 results positive; stock up 4% pre-market",
        "Pfizer restructures R&D after COVID-19 revenue normalization; targets $4B in savings",
        "FDA approves novel Alzheimer's treatment; healthcare sector ETF gains 2.3%",
        "UnitedHealth raises guidance after Q1 claims ratio improves 120bps YoY",
    ],
    "energy": [
        "Exxon Mobil cuts 2026 capex by $2B amid oil price volatility; maintains dividend",
        "OPEC+ extends production cuts through Q3 2026; Brent crude holds at $82/barrel",
        "Solar and wind capacity additions hit global record in Q1 2026; utilities outperform",
        "Natural gas prices fall 8% on mild weather; US LNG export volumes remain strong",
    ],
    "financials": [
        "JPMorgan Q1 profits rise 6% on higher interest income; NIM expands 12bps to 2.81%",
        "Goldman Sachs investment banking revenue surges 34% as IPO market reopens",
        "Federal Reserve signals two rate cuts in 2026; bank stocks rally on improved NIMs",
        "Bank of America deposit growth stabilizes; commercial loan losses below guidance",
    ],
    "consumer_discretionary": _CONSUMER_DISCRETIONARY_NEWS,
    "consumer_staples": _CONSUMER_STAPLES_NEWS,
    "consumer": _CONSUMER_DISCRETIONARY_NEWS + _CONSUMER_STAPLES_NEWS,
    "macro": [
        "US CPI cools to 2.8% in March; Fed June rate cut probability rises to 72%",
        "Q1 GDP growth revised to +2.4% annualized; labor market adds 180K non-farm payrolls",
        "10-year Treasury yield at 4.32% as term premium rebuilds on fiscal deficit concerns",
        "US-China trade talks resume; targeted tariff reductions signal improving relations",
    ],
}

_SECTOR_DATA: dict[str, dict[str, Any]] = {
    "technology": {
        "trend": "Outperforming (+1.24% today, +8.3% YTD)",
        "key_themes": [
            "AI infrastructure buildout driving hyperscaler capex ($200B+ in 2026)",
            "Semiconductor supercycle: memory pricing recovery + AI chip demand",
            "Cloud computing compounding at 25-30% YoY across major providers",
        ],
        "risks": [
            "Premium valuations (sector P/E 34x vs S&P 500 22x)",
            "Regulatory scrutiny: EU AI Act, US antitrust probes",
            "Export restrictions on advanced chips to China",
        ],
        "outlook": ("Bullish. AI demand is a multi-year structural driver. Prefer NVDA, MSFT, GOOGL."),
        "top_holdings": ["NVDA", "MSFT", "AAPL", "GOOGL", "META"],
    },
    "healthcare": {
        "trend": "Neutral (+0.31% today, +2.1% YTD)",
        "key_themes": [
            "GLP-1 obesity/diabetes drugs (Eli Lilly, Novo Nordisk) reshaping pharma",
            "AI-accelerated drug discovery compressing development timelines",
            "Biosimilar competition eroding revenue for legacy biologics",
        ],
        "risks": [
            "Medicare drug price negotiation under IRA limiting pharma margins",
            "Patent cliff: $200B+ revenues exposed through 2028",
            "Clinical trial failures in oncology and CNS pipelines",
        ],
        "outlook": ("Selective. Biotech innovation vs. pharma headwinds. Overweight LLY, UNH; underweight PFE."),
        "top_holdings": ["LLY", "UNH", "JNJ", "ABBV", "MRK"],
    },
    "energy": {
        "trend": "Underperforming (-1.12% today, -3.4% YTD)",
        "key_themes": [
            "Energy transition accelerating renewable deployment globally",
            "LNG infrastructure investment surge for energy security",
        ],
        "risks": [
            "Oil price compression from rising supply and demand uncertainty",
            "ESG capital allocation shifts reducing fossil fuel investment",
            "Geopolitical supply disruptions in Middle East and Russia",
        ],
        "outlook": ("Mixed. Avoid pure-play oil. Prefer diversified energy with renewable exposure (XOM, CVX)."),
        "top_holdings": ["XOM", "CVX", "COP", "EOG", "SLB"],
    },
    "financials": {
        "trend": "Improving (+0.67% today, +5.8% YTD)",
        "key_themes": [
            "Rate normalization cycle supporting net interest margins",
            "Investment banking recovery: IPO pipeline strongest since 2021",
        ],
        "risks": [
            "Credit quality deterioration in commercial real estate",
            "Basel III endgame capital requirements reducing returns",
        ],
        "outlook": "Cautiously optimistic. Rate cuts are net positive. Prefer JPM, GS.",
        "top_holdings": ["JPM", "BAC", "GS", "MS", "BLK"],
    },
    "consumer_staples": {
        "trend": "Stable (+0.22% today, +1.9% YTD)",
        "key_themes": ["Pricing power normalization as input cost inflation eases"],
        "risks": ["Consumer trade-down to private labels compressing margins"],
        "outlook": ("Defensive. Attractive for risk-off positioning. Focus on dividend growers: KO, PG."),
        "top_holdings": ["KO", "PG", "PEP", "COST", "WMT"],
    },
    "consumer_discretionary": {
        "trend": "Mixed (-0.45% today, +3.1% YTD)",
        "key_themes": ["E-commerce dominance with Amazon capturing 38% of US online sales"],
        "risks": [
            "Consumer spending slowdown if labor market softens",
            "Tesla competitive pressure from BYD and legacy OEMs",
        ],
        "outlook": "Selective. AMZN is core holding. Avoid pure EV plays; cautious on TSLA.",
        "top_holdings": ["AMZN", "HD", "MCD", "SBUX", "TSLA"],
    },
}


@tool
def get_stock_data(symbol: str) -> dict[str, Any]:
    """Get current stock price and market data for a given ticker symbol.

    Args:
        symbol: Stock ticker symbol (AAPL, MSFT, GOOGL, AMZN, TSLA, NVDA, META,
            JPM, GS, JNJ, LLY, XOM, CVX, KO, PG, SPY).

    Returns:
        Price, change, sector, market cap, P/E ratio, and dividend yield.
    """
    normalized_symbol = symbol.upper().replace(".", "")
    data = _STOCKS.get(normalized_symbol) or _STOCKS.get(symbol.upper())
    if not data:
        return {
            "error": f"Symbol '{symbol}' not found.",
            "supported_symbols": list(_STOCKS.keys()),
        }
    return {"symbol": normalized_symbol, **data}


@tool
def search_news(query: str, sector: str = "macro") -> dict[str, Any]:
    """Search for recent market news headlines.

    Args:
        query: Topic or keyword, such as ``AI chips``, ``rate cuts``, or
            ``earnings``.
        sector: technology, healthcare, energy, financials,
            consumer_discretionary, consumer_staples, consumer, or macro.

    Returns:
        The resolved deterministic news sector and matching headlines. Unknown
        sectors resolve to the macro bucket.
    """
    requested_key = sector.lower().replace(" ", "_").replace("-", "_")
    key = requested_key if requested_key in _NEWS else "macro"
    return {"query": query, "sector": key, "headlines": _NEWS[key]}


@tool
def get_market_overview() -> dict[str, Any]:
    """Get a snapshot of major indices, sector performance, and top movers."""
    return {
        "indices": {
            "S&P 500": {"level": 5234.18, "change_pct": +0.42, "ytd_pct": +7.8},
            "NASDAQ": {"level": 16421.54, "change_pct": +0.87, "ytd_pct": +9.3},
            "Dow Jones": {"level": 38742.15, "change_pct": +0.18, "ytd_pct": +4.1},
            "VIX": {"level": 14.23, "change_pct": -3.21, "note": "low volatility"},
        },
        "sector_performance_today": {
            "Technology": +1.24,
            "Healthcare": +0.31,
            "Financials": +0.67,
            "Energy": -1.12,
            "Consumer Discretionary": -0.45,
            "Consumer Staples": +0.22,
        },
        "top_gainers": [
            {"symbol": "NVDA", "change_pct": +2.76},
            {"symbol": "AMZN", "change_pct": +2.70},
        ],
        "top_losers": [
            {"symbol": "TSLA", "change_pct": -3.35},
            {"symbol": "XOM", "change_pct": -1.82},
        ],
        "market_sentiment": "Moderately Bullish",
    }


@tool
def get_sector_data(sector: str) -> dict[str, Any]:
    """Get detailed analysis for a market sector.

    Args:
        sector: technology, healthcare, energy, financials, consumer_staples,
            or consumer_discretionary.

    Returns:
        Trend, key themes, risks, outlook, and top holdings.
    """
    key = sector.lower().replace(" ", "_").replace("-", "_")
    data = _SECTOR_DATA.get(key)
    if not data:
        return {
            "error": f"Sector '{sector}' not found.",
            "available": list(_SECTOR_DATA.keys()),
        }
    return {"sector": key, **data}


@tool
def compare_stocks(symbols: str) -> dict[str, Any]:
    """Compare multiple stocks side by side on key metrics.

    Args:
        symbols: Comma-separated ticker symbols, such as ``AAPL,MSFT,GOOGL``.

    Returns:
        A comparison table and count of matched symbols.
    """
    result: dict[str, dict[str, Any]] = {}
    for symbol in [item.strip().upper() for item in symbols.split(",")]:
        data = _STOCKS.get(symbol.replace(".", "")) or _STOCKS.get(symbol)
        if data:
            result[symbol] = {
                "name": data["name"],
                "price": data["price"],
                "change_pct": data["change_pct"],
                "sector": data["sector"],
                "pe_ratio": data["pe_ratio"],
                "dividend_yield": data["dividend_yield"],
            }
    return {"comparison": result, "count": len(result)}


SYSTEM_PROMPT = """\
You are an expert market intelligence analyst for MarketPulse Pro, providing
financial insights to investment brokers and advisors.

IMPORTANT: You have access to structured market analysis skills (listed in the
<available_skills> section above). Before performing any specialised analysis
task, ALWAYS activate the relevant skill first using the `skills` tool:

  skills(skill_name="trend-analysis")    — price trend / momentum
  skills(skill_name="sector-rotation")   — sector rotation / allocation
  skills(skill_name="earnings-snapshot") — earnings / valuation assessment
  skills(skill_name="portfolio-risk")    — portfolio risk evaluation

After activating a skill, follow its instructions precisely and use the tools
it specifies. For general market-overview requests, use the ordinary market
tools directly without activating a skill.

Always include specific numbers from tool results in your responses.
"""

_MAX_SESSION_AGENTS = 32
_SESSION_AGENTS: OrderedDict[str, Agent] = OrderedDict()
_SESSION_AGENTS_LOCK = Lock()


def _create_agent() -> Agent:
    """Create a session-local Strands agent without making a model request."""
    return Agent(
        model=_MODEL,
        tools=[
            get_stock_data,
            search_news,
            get_market_overview,
            get_sector_data,
            compare_stocks,
        ],
        plugins=[_SKILLS_PLUGIN],
        system_prompt=SYSTEM_PROMPT,
    )


def _get_agent(session_id: str | None) -> Agent:
    """Return the cached agent for a session, or an uncached safe fallback."""
    if not session_id:
        return _create_agent()

    with _SESSION_AGENTS_LOCK:
        agent = _SESSION_AGENTS.get(session_id)
        if agent is None:
            agent = _create_agent()
            _SESSION_AGENTS[session_id] = agent
            if len(_SESSION_AGENTS) > _MAX_SESSION_AGENTS:
                _SESSION_AGENTS.popitem(last=False)
        else:
            _SESSION_AGENTS.move_to_end(session_id)
        return agent


def _safe_string(value: Any) -> str:
    """Convert streamed values to text without propagating conversion errors."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return str(value)


@app.entrypoint
async def invoke(payload: Mapping[str, Any], context: Any) -> str:
    """Invoke the session-scoped MarketTrends agent and collect text chunks."""
    prompt = _safe_string(payload.get("prompt", ""))
    raw_session_id = getattr(context, "session_id", None)
    session_id = _safe_string(raw_session_id) if raw_session_id is not None else None
    logger.info("Received prompt (session=%s): %s", session_id, prompt[:80])

    agent = _get_agent(session_id)
    chunks: list[str] = []
    async for event in agent.stream_async(prompt):
        if isinstance(event, Mapping) and "data" in event:
            chunk = _safe_string(event["data"])
            if chunk:
                chunks.append(chunk)
    return "".join(chunks)


if __name__ == "__main__":
    app.run()
