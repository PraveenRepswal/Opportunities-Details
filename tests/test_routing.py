import os
os.environ["NEEDLE_TELEMETRY"] = "0"

from unittest.mock import MagicMock, patch
import pytest
from langchain_core.documents import Document
try:
    from needle import Needle, tool
except ImportError:
    Needle = None  # type: ignore
    def tool(f):
        return f

from backend.agent import (
    OpportunityAgentGraph,
    has_opportunity_intent,
    is_casual_chat,
    is_simple_opportunity_query,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ============================================================================
# Keyword heuristics (casual chat / opportunity intent / simple-query bypass)
# ============================================================================

class TestCasualChat:
    def test_casual_greetings_detected(self):
        for text in ("hi", "hello", "hey!", "Who are you?", "Good morning"):
            assert is_casual_chat(text) is True

    def test_opportunity_prompts_are_not_casual(self):
        for text in (
            "Tell me about DAAD scholarships",
            "Are there internships in Germany?",
            "What is the deadline for this program?",
            "PhD funding options",
        ):
            assert is_casual_chat(text) is False
            assert has_opportunity_intent(text) is True

    def test_noise_words_no_longer_trigger_intent(self):
        assert has_opportunity_intent("send me the link") is False
        assert has_opportunity_intent("how many days in a week") is False


class TestIsSimpleOpportunityQuery:
    def test_simple_matches(self):
        assert is_simple_opportunity_query("Are there any scholarships in France?") is True
        assert is_simple_opportunity_query("Find master programs in Germany") is True
        assert is_simple_opportunity_query("DAAD application details") is True
        assert is_simple_opportunity_query("Summer internship opportunities") is True
        assert is_simple_opportunity_query("Postdoc fellowships available") is True
        assert is_simple_opportunity_query("Research grants for biology") is True

    def test_explicit_web_intent_skips_fast_bypass(self):
        assert is_simple_opportunity_query("Search the web for 2027 Rhodes scholarship") is False
        assert is_simple_opportunity_query("Search online for MIT fellowships") is False
        assert is_simple_opportunity_query("Google scholarships in Canada") is False
        assert is_simple_opportunity_query("Browse the web for updates on DAAD") is False

    def test_negation_and_jokes_skip_fast_bypass(self):
        assert is_simple_opportunity_query("I don't want scholarships, tell me a joke") is False
        assert is_simple_opportunity_query("Not looking for internships, write a poem") is False

    def test_general_queries_skip_fast_bypass(self):
        assert is_simple_opportunity_query("What is quantum computing?") is False
        assert is_simple_opportunity_query("Can you help me write an essay?") is False
        assert is_simple_opportunity_query("Who was Albert Einstein?") is False


@pytest.mark.anyio
class TestAgentWorkflowFastBypass:
    async def test_casual_chat_uses_no_tools(self):
        graph = OpportunityAgentGraph(MagicMock())
        result = await graph.run_agent_workflow("hello")
        assert result["has_tool_call"] is False
        assert result["used_tools"] == []
        assert result["context_text"] == ""

    async def test_negation_uses_no_tools(self):
        graph = OpportunityAgentGraph(MagicMock())
        result = await graph.run_agent_workflow("I don't want scholarships, tell me a joke")
        assert result["has_tool_call"] is False

    async def test_fast_bypass_hit_avoids_llm_invocation(self):
        mock_pipeline = MagicMock()
        mock_pipeline.reranker = None
        graph = OpportunityAgentGraph(mock_pipeline)

        sample_doc = Document(
            page_content="DAAD scholarship offers monthly stipend for students in Germany.",
            metadata={"name": "DAAD Postgraduate Scholarship", "deadline": "2026-10-15"},
        )
        graph.search_local_opportunities = MagicMock(return_value=[sample_doc])
        mock_pipeline.get_llm = MagicMock()

        result = await graph.run_agent_workflow("Tell me about DAAD scholarships in Germany")

        assert result["has_tool_call"] is True
        assert "search_local_opportunities" in result["used_tools"]
        assert "DAAD Postgraduate Scholarship" in result["retrieved_docs"]
        assert "DAAD" in result["context_text"]
        mock_pipeline.get_llm.assert_not_called()

    async def test_fast_bypass_miss_falls_through_to_web_search(self):
        mock_pipeline = MagicMock()
        mock_pipeline.reranker = None
        mock_pipeline.get_llm = MagicMock()
        graph = OpportunityAgentGraph(mock_pipeline)
        graph.search_local_opportunities = MagicMock(return_value=[])

        with patch("backend.agent.search_live_web", return_value="Found 1 result online"):
            result = await graph.run_agent_workflow("Obscure scholarship in Chile")

            mock_pipeline.get_llm.assert_not_called()
            assert result["has_tool_call"] is True
            assert "search_live_web" in result["used_tools"]


# ============================================================================
# Needle SLM Router & Deep-Dive Dossier Tests
# ============================================================================

@tool
def search_opportunities(query: str):
    """Search database for scholarships, fellowships, internships, or programs by country, degree, or keyword."""
    pass


@tool
def lookup_opportunity_details(opportunity: str):
    """Get full details, requirements, eligibility, deadlines, or application instructions for a specific named scholarship or program."""
    pass


@tool
def search_web(query: str):
    """Search online on the web for live internet information, external news, or outside topics."""
    pass


@pytest.fixture(scope="module")
def needle_agent():
    return Needle(
        tools=[search_opportunities, lookup_opportunity_details, search_web],
        stateless=True,
    )


class TestNeedleRouting:
    def test_deep_dive_routing(self, needle_agent):
        res = needle_agent.complete("Tell me all details about Max Planck MTL Internship")
        calls = res.get("function_calls", [])
        assert len(calls) == 1
        assert calls[0]["name"] == "lookup_opportunity_details"
        assert "Max Planck" in calls[0]["arguments"]["opportunity"]

    def test_broad_search_routing(self, needle_agent):
        res = needle_agent.complete("Find internships in Germany for undergraduate students")
        calls = res.get("function_calls", [])
        assert len(calls) == 1
        assert calls[0]["name"] == "search_opportunities"

    def test_casual_chat_routing(self, needle_agent):
        res = needle_agent.complete("Hey Anna, how are you today?")
        assert len(res.get("function_calls", [])) == 0

    def test_unrelated_coding_routing(self, needle_agent):
        res = needle_agent.complete("Write python code to compute fibonacci numbers")
        assert len(res.get("function_calls", [])) == 0

    def test_needle_router_class_route(self):
        from backend.agent import get_needle_router
        router = get_needle_router()
        assert router.is_available is True
        decision = router.route("Tell me all details about Max Planck MTL Internship in Germany")
        assert decision.tool_name == "lookup_opportunity_details"
        assert "Max Planck" in decision.arguments.get("opportunity", "")


class TestDeepDiveResolution:
    def test_resolve_opportunity_deep_dive(self):
        from backend.agent import format_deep_dive_dossier, resolve_opportunity_deep_dive
        opp = resolve_opportunity_deep_dive("Max Planck MTL")
        assert opp is not None
        assert "max planck" in opp["title"].lower()
        assert len(opp["content"]) > 1000  # full un-truncated parent content

        opp_conversational = resolve_opportunity_deep_dive("give me full details about World Bank")
        assert opp_conversational is not None
        assert "world bank" in opp_conversational["title"].lower()

        dossier = format_deep_dive_dossier(opp)
        assert "=== FULL APPLICATION DOSSIER:" in dossier
        assert "Official Link:" in dossier
        assert "Application Deadline:" in dossier

    @pytest.mark.anyio
    async def test_agent_workflow_needle_deep_dive(self):
        mock_pipeline = MagicMock()
        mock_pipeline.get_llm = MagicMock()
        graph = OpportunityAgentGraph(mock_pipeline)

        result = await graph.run_agent_workflow("Tell me all details about Max Planck MTL Internship in Germany")
        assert result["has_tool_call"] is True
        assert "lookup_opportunity_details" in result["used_tools"]
        assert len(result["context_text"]) > 1000
        assert "MAX PLANCK" in result["context_text"].upper()
        mock_pipeline.get_llm.assert_not_called()

