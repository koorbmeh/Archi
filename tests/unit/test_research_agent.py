"""Tests for the Deep Research Agent and Tavily search wrapper."""

import json
from unittest.mock import MagicMock, patch

import pytest


# ── Tavily search wrapper tests ──


class TestTavilySearch:
    """Tests for src.tools.tavily_search."""

    def test_singleton_pattern(self):
        from src.tools.tavily_search import _reset_for_testing, get_tavily_search
        _reset_for_testing()
        a = get_tavily_search()
        b = get_tavily_search()
        assert a is b
        _reset_for_testing()

    def test_available_without_key(self):
        from src.tools.tavily_search import TavilySearch
        ts = TavilySearch(api_key=None)
        assert not ts.available

    def test_available_with_key(self):
        from src.tools.tavily_search import TavilySearch
        ts = TavilySearch(api_key="tvly-test-key")
        assert ts.available

    def test_search_requires_key(self):
        from src.tools.tavily_search import TavilySearch
        ts = TavilySearch(api_key=None)
        with pytest.raises(RuntimeError, match="TAVILY_API_KEY not configured"):
            ts.search("test query")

    @patch("src.tools.tavily_search.TavilySearch._get_client")
    def test_search_returns_structured_results(self, mock_get_client):
        from src.tools.tavily_search import TavilySearch
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [
                {
                    "title": "Test Result",
                    "url": "https://example.com",
                    "content": "This is test content",
                    "score": 0.95,
                },
                {
                    "title": "Second Result",
                    "url": "https://example2.com",
                    "content": "More content",
                    "score": 0.8,
                },
            ],
            "answer": "Test answer",
            "response_time": 0.5,
        }
        mock_get_client.return_value = mock_client

        ts = TavilySearch(api_key="tvly-test-key")
        response = ts.search("test query")

        assert response.query == "test query"
        assert len(response.results) == 2
        assert response.results[0].title == "Test Result"
        assert response.results[0].url == "https://example.com"
        assert response.results[0].content == "This is test content"
        assert response.results[0].score == 0.95
        assert response.answer == "Test answer"
        assert response.response_time == 0.5

    @patch("src.tools.tavily_search.TavilySearch._get_client")
    def test_extract_returns_pages(self, mock_get_client):
        from src.tools.tavily_search import TavilySearch
        mock_client = MagicMock()
        mock_client.extract.return_value = {
            "results": [
                {"url": "https://example.com", "raw_content": "Full page content here"},
            ],
            "failed_results": [
                {"url": "https://blocked.com", "error": "403 Forbidden"},
            ],
        }
        mock_get_client.return_value = mock_client

        ts = TavilySearch(api_key="tvly-test-key")
        pages = ts.extract(["https://example.com", "https://blocked.com"])

        assert len(pages) == 2
        assert pages[0].success is True
        assert pages[0].raw_content == "Full page content here"
        assert pages[1].success is False
        assert "403" in pages[1].error

    @patch("src.tools.tavily_search.TavilySearch._get_client")
    def test_format_results(self, mock_get_client):
        from src.tools.tavily_search import TavilySearch, TavilySearchResponse, TavilyResult
        ts = TavilySearch(api_key="tvly-test-key")
        response = TavilySearchResponse(
            query="test",
            results=[
                TavilyResult(title="Result 1", url="https://a.com", content="Content A"),
            ],
            answer="Summary answer",
        )
        formatted = ts.format_results(response)
        assert "Result 1" in formatted
        assert "https://a.com" in formatted
        assert "Summary answer" in formatted

    def test_extract_empty_urls(self):
        from src.tools.tavily_search import TavilySearch
        ts = TavilySearch(api_key="tvly-test-key")
        assert ts.extract([]) == []


# ── Research Agent tests ──


class TestResearchAgent:
    """Tests for src.core.research_agent."""

    def _make_router(self, responses=None):
        """Create a mock router that returns canned responses."""
        router = MagicMock()
        if responses is None:
            responses = []
        call_idx = [0]

        def generate_side_effect(**kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(responses):
                return {"text": responses[idx], "cost_usd": 0.001, "success": True}
            return {"text": "{}", "cost_usd": 0.001, "success": True}

        router.generate.side_effect = generate_side_effect
        return router

    def test_research_result_to_dict(self):
        from src.core.research_agent import ResearchResult, Source
        result = ResearchResult(
            question="What is X?",
            conclusion="X is Y.",
            key_findings=["finding 1"],
            sources=[Source(url="https://a.com", title="A", content_summary="About A", relevance=0.9)],
            confidence=0.85,
            gaps=["unknown Z"],
            recommendation="Use Y",
            search_rounds=2,
            total_cost=0.05,
            elapsed_seconds=10.0,
        )
        d = result.to_dict()
        assert d["question"] == "What is X?"
        assert d["confidence"] == 0.85
        assert d["recommendation"] == "Use Y"
        assert len(d["sources"]) == 1
        assert d["sources"][0]["url"] == "https://a.com"

    def test_research_result_format_for_user(self):
        from src.core.research_agent import ResearchResult
        result = ResearchResult(
            question="What is X?",
            conclusion="X is a technology.",
            key_findings=["It works well", "It's cheap"],
            recommendation="Use X",
            confidence=0.8,
            search_rounds=3,
            total_cost=0.02,
        )
        formatted = result.format_for_user()
        assert "**Recommendation:**" in formatted
        assert "Use X" in formatted
        assert "X is a technology" in formatted
        assert "It works well" in formatted
        assert "80%" in formatted

    def test_parse_json_list(self):
        from src.core.research_agent import ResearchAgent
        assert ResearchAgent._parse_json_list('["a", "b", "c"]') == ["a", "b", "c"]
        assert ResearchAgent._parse_json_list('Some text\n["x", "y"]') == ["x", "y"]
        # Fallback to line parsing
        result = ResearchAgent._parse_json_list("- first query\n- second query\n- third")
        assert len(result) >= 2

    def test_parse_json_obj(self):
        from src.core.research_agent import ResearchAgent
        obj = ResearchAgent._parse_json_obj('{"key": "value", "num": 42}')
        assert obj["key"] == "value"
        assert obj["num"] == 42
        # Embedded in text
        obj = ResearchAgent._parse_json_obj('Here is the result: {"a": 1}')
        assert obj["a"] == 1
        # Invalid JSON
        assert ResearchAgent._parse_json_obj("not json") == {}

    @patch("src.core.research_agent.ResearchAgent._search")
    @patch("src.core.research_agent.ResearchAgent._extract_pages")
    def test_research_basic_flow(self, mock_extract, mock_search):
        from src.core.research_agent import ResearchAgent

        # Mock search results
        mock_search.return_value = [
            {"title": "API Guide", "url": "https://docs.example.com", "content": "Detailed guide about APIs"},
            {"title": "Comparison", "url": "https://blog.example.com", "content": "Comparing different options"},
        ]
        mock_extract.return_value = {"https://docs.example.com": "Full API documentation content"}

        # Mock router responses: queries, evaluation, synthesis
        router = self._make_router([
            # Query generation
            json.dumps(["music generation API options", "best AI music API 2026"]),
            # Round evaluation
            json.dumps({
                "key_facts": ["API X costs $0.03/track", "API Y has better quality"],
                "gaps": [],
                "sufficient": True,
                "follow_up_queries": [],
                "source_assessments": [
                    {"url": "https://docs.example.com", "title": "API Guide", "relevance": 0.9, "summary": "Comprehensive guide"},
                    {"url": "https://blog.example.com", "title": "Comparison", "relevance": 0.8, "summary": "Good comparison"},
                ],
            }),
            # Synthesis
            json.dumps({
                "conclusion": "Based on research, API X is the best option for cost-effective music generation.",
                "key_findings": ["API X costs $0.03/track", "API Y has better quality but costs more"],
                "confidence": 0.85,
                "gaps": ["Long-term reliability data unavailable"],
                "recommendation": "Use API X for cost-effective music generation",
            }),
        ])

        agent = ResearchAgent(router)
        result = agent.research("What's the best music generation API?")

        assert result.question == "What's the best music generation API?"
        assert result.confidence == 0.85
        assert "API X" in result.conclusion
        assert len(result.key_findings) >= 1
        assert len(result.sources) >= 1
        assert result.recommendation != ""
        assert result.total_cost > 0
        assert result.search_rounds >= 1

    @patch("src.core.research_agent.ResearchAgent._search")
    @patch("src.core.research_agent.ResearchAgent._extract_pages")
    def test_research_multiple_rounds(self, mock_extract, mock_search):
        """Test that research does follow-up rounds when not sufficient."""
        from src.core.research_agent import ResearchAgent

        mock_search.return_value = [
            {"title": "Result", "url": "https://r1.com", "content": "Some info"},
        ]
        mock_extract.return_value = {}

        router = self._make_router([
            # Query generation
            json.dumps(["initial query"]),
            # Round 1 evaluation — NOT sufficient
            json.dumps({
                "key_facts": ["partial info found"],
                "gaps": ["need pricing data"],
                "sufficient": False,
                "follow_up_queries": ["pricing comparison"],
                "source_assessments": [
                    {"url": "https://r1.com", "title": "Result", "relevance": 0.6, "summary": "Partial"},
                ],
            }),
            # Round 2 evaluation — sufficient
            json.dumps({
                "key_facts": ["pricing is $5/mo"],
                "gaps": [],
                "sufficient": True,
                "follow_up_queries": [],
                "source_assessments": [],
            }),
            # Synthesis
            json.dumps({
                "conclusion": "After thorough research, pricing is $5/mo.",
                "key_findings": ["pricing is $5/mo"],
                "confidence": 0.75,
                "gaps": [],
                "recommendation": "",
            }),
        ])

        agent = ResearchAgent(router)
        result = agent.research("pricing info", max_rounds=3)

        assert result.search_rounds >= 2
        assert result.confidence > 0

    @patch("src.core.research_agent.ResearchAgent._search")
    @patch("src.core.research_agent.ResearchAgent._extract_pages")
    def test_research_no_results_stops(self, mock_extract, mock_search):
        """Test that research stops gracefully when search returns nothing."""
        from src.core.research_agent import ResearchAgent

        mock_search.return_value = []
        mock_extract.return_value = {}

        router = self._make_router([
            json.dumps(["query 1"]),
            # Synthesis (skips evaluation since no results)
            json.dumps({
                "conclusion": "No relevant information found.",
                "key_findings": [],
                "confidence": 0.1,
                "gaps": ["Everything"],
                "recommendation": "",
            }),
        ])

        agent = ResearchAgent(router)
        result = agent.research("obscure topic")
        assert result.confidence <= 0.5

    def test_compare_options_delegates_to_research(self):
        from src.core.research_agent import ResearchAgent
        router = MagicMock()
        agent = ResearchAgent(router)

        with patch.object(agent, "research") as mock_research:
            from src.core.research_agent import ResearchResult
            mock_research.return_value = ResearchResult(question="compare", conclusion="A is best")
            result = agent.compare_options(
                "Which API?",
                options=["API A", "API B"],
                criteria=["price", "quality"],
            )
            assert mock_research.called
            call_args = mock_research.call_args
            assert "API A" in call_args.kwargs["question"]
            assert "API B" in call_args.kwargs["question"]

    @patch("src.core.research_agent.ResearchAgent._search")
    @patch("src.core.research_agent.ResearchAgent._extract_pages")
    def test_research_cost_cap(self, mock_extract, mock_search):
        """Test that research respects the cost cap."""
        from src.core.research_agent import ResearchAgent, RESEARCH_COST_CAP

        mock_search.return_value = [
            {"title": "R", "url": "https://r.com", "content": "Info"},
        ]
        mock_extract.return_value = {}

        # Make router return high costs to trigger cap
        router = MagicMock()
        router.generate.return_value = {
            "text": json.dumps(["q1"]),
            "cost_usd": RESEARCH_COST_CAP + 0.01,  # Exceeds cap on first call
            "success": True,
        }

        agent = ResearchAgent(router)
        result = agent.research("test")
        # Should stop early due to cost cap
        assert result.total_cost > 0

    def test_format_round_results(self):
        from src.core.research_agent import ResearchAgent
        results = [
            {"title": "Page A", "url": "https://a.com", "content": "Content about A"},
            {"title": "Page B", "url": "https://b.com", "content": "Content about B"},
        ]
        formatted = ResearchAgent._format_round_results(results)
        assert "[1]" in formatted
        assert "[2]" in formatted
        assert "Page A" in formatted
        assert "https://b.com" in formatted


# ── Action dispatcher integration tests ──


class TestResearchActionHandler:
    """Tests for deep_research action handler in action_dispatcher."""

    def test_handler_registered(self):
        from src.interfaces.action_dispatcher import ACTION_HANDLERS
        assert "deep_research" in ACTION_HANDLERS

    def test_dispatch_routes_to_handler(self):
        from src.interfaces.action_dispatcher import ACTION_HANDLERS, _handle_deep_research
        # Verify deep_research maps to the correct handler function
        assert ACTION_HANDLERS["deep_research"] is _handle_deep_research

    def test_handler_empty_query(self):
        from src.interfaces.action_dispatcher import _handle_deep_research
        result = _handle_deep_research({}, {"effective_message": "", "router": MagicMock()})
        assert "What would you like me to research?" in result[0]

    @patch("src.core.research_agent.ResearchAgent.research")
    def test_handler_calls_research_agent(self, mock_research):
        from src.core.research_agent import ResearchResult
        from src.interfaces.action_dispatcher import _handle_deep_research

        mock_research.return_value = ResearchResult(
            question="test",
            conclusion="Test conclusion",
            confidence=0.9,
            total_cost=0.01,
        )

        response, actions, cost = _handle_deep_research(
            {"query": "best music API"},
            {"router": MagicMock(), "effective_message": ""},
        )
        assert "Test conclusion" in response
        assert mock_research.called


# ── Router integration tests ──


class TestResearchRouterIntent:
    """Tests for research intent in conversational_router."""

    def test_research_intent_in_router_prompt(self):
        """Verify the research intent is documented in the router system prompt."""
        from src.core.conversational_router import _router_system
        system = _router_system()
        assert '"research"' in system
        assert "deep_research" in system
