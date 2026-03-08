"""
Deep Research Agent — multi-round web research with synthesis.

Upgrades Archi from "search and get snippets" to "research a topic thoroughly."
Uses Tavily for search + extraction, with DuckDuckGo fallback. Model calls for
query generation, evaluation, and synthesis.

Architecture:
    1. Generate initial search queries from the question
    2. Search (Tavily preferred, DDG fallback)
    3. Read top results (extract useful content)
    4. Evaluate: enough info? What's still unclear?
    5. If gaps remain, generate follow-up queries → go to 2
    6. Synthesize findings into structured conclusion
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Budget and limit constants
MAX_SEARCH_ROUNDS = 5
MAX_SOURCES = 10
MAX_EXTRACT_PER_ROUND = 3
RESEARCH_COST_CAP = 0.15  # USD — keep research affordable


@dataclass
class Source:
    """A web source consulted during research."""
    url: str = ""
    title: str = ""
    content_summary: str = ""  # What we learned from this source
    relevance: float = 0.0  # 0-1, how relevant to the question


@dataclass
class ResearchResult:
    """Complete output of a research session."""
    question: str = ""
    conclusion: str = ""  # 2-3 paragraph synthesis
    key_findings: List[str] = field(default_factory=list)
    sources: List[Source] = field(default_factory=list)
    confidence: float = 0.0  # 0-1, how confident in conclusion
    gaps: List[str] = field(default_factory=list)  # What couldn't be determined
    recommendation: str = ""  # If question asks "which should we use?"
    search_rounds: int = 0
    total_cost: float = 0.0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for storage/logging."""
        return {
            "question": self.question,
            "conclusion": self.conclusion,
            "key_findings": self.key_findings,
            "sources": [{"url": s.url, "title": s.title, "summary": s.content_summary} for s in self.sources],
            "confidence": self.confidence,
            "gaps": self.gaps,
            "recommendation": self.recommendation,
            "search_rounds": self.search_rounds,
            "total_cost": self.total_cost,
            "elapsed_seconds": self.elapsed_seconds,
        }

    def format_for_user(self) -> str:
        """Format as a readable summary for Discord delivery."""
        parts = []
        if self.recommendation:
            parts.append(f"**Recommendation:** {self.recommendation}\n")
        parts.append(self.conclusion)
        if self.key_findings:
            parts.append("\n**Key findings:**")
            for f in self.key_findings[:8]:
                parts.append(f"• {f}")
        if self.gaps:
            parts.append("\n**Still unclear:**")
            for g in self.gaps[:4]:
                parts.append(f"• {g}")
        parts.append(f"\n*Confidence: {self.confidence:.0%} | {len(self.sources)} sources | {self.search_rounds} search rounds | ${self.total_cost:.4f}*")
        return "\n".join(parts)


class ResearchAgent:
    """Multi-round web research with synthesis.

    Uses Tavily for search/extraction when available, DuckDuckGo fallback.
    Model calls (via router) for query generation, evaluation, and synthesis.
    """

    def __init__(self, router: Any) -> None:
        """Initialize with a model router for LLM calls.

        Args:
            router: The Archi model router (src.models.router.ModelRouter).
        """
        self._router = router
        self._tavily: Any = None
        self._ddg: Any = None

    def _get_tavily(self) -> Any:
        """Lazy-init Tavily search."""
        if self._tavily is None:
            from src.tools.tavily_search import get_tavily_search
            self._tavily = get_tavily_search()
        return self._tavily

    def _get_ddg(self) -> Any:
        """Lazy-init DuckDuckGo search."""
        if self._ddg is None:
            from src.tools.web_search_tool import WebSearchTool
            self._ddg = WebSearchTool()
        return self._ddg

    def _search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Search using best available backend. Returns list of {title, url, content}."""
        tavily = self._get_tavily()
        if tavily.available:
            try:
                response = tavily.search(
                    query=query,
                    search_depth="basic",
                    max_results=max_results,
                    include_answer=False,
                )
                return [
                    {"title": r.title, "url": r.url, "content": r.content}
                    for r in response.results
                ]
            except Exception as e:
                logger.warning("Tavily search failed, falling back to DDG: %s", e)
        # Fallback to DuckDuckGo
        ddg = self._get_ddg()
        results = ddg.search(query, max_results=max_results)
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("snippet", "")}
            for r in results
        ]

    def _extract_pages(self, urls: List[str]) -> Dict[str, str]:
        """Extract full content from URLs. Returns {url: content}."""
        tavily = self._get_tavily()
        extracted = {}
        if tavily.available and urls:
            try:
                pages = tavily.extract(urls[:MAX_EXTRACT_PER_ROUND])
                for page in pages:
                    if page.success and page.raw_content:
                        # Truncate to keep context manageable
                        extracted[page.url] = page.raw_content[:3000]
            except Exception as e:
                logger.warning("Tavily extract failed: %s", e)
        return extracted

    def _model_call(self, system: str, prompt: str, max_tokens: int = 800) -> tuple:
        """Make a model call via router. Returns (text, cost)."""
        result = self._router.generate(
            system_prompt=system,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=0.3,
            skip_web_search=True,
        )
        return (result.get("text", ""), result.get("cost_usd", 0.0))

    def research(
        self,
        question: str,
        context: str = "",
        max_rounds: int = MAX_SEARCH_ROUNDS,
        max_sources: int = MAX_SOURCES,
    ) -> ResearchResult:
        """Execute a multi-round research process.

        Args:
            question: The research question (e.g. "What's the best music gen API?")
            context: Background info, constraints, budget.
            max_rounds: Max search-read-evaluate cycles.
            max_sources: Max total pages to consult.

        Returns:
            ResearchResult with synthesized findings.
        """
        start = time.monotonic()
        total_cost = 0.0
        all_sources: List[Source] = []
        accumulated_knowledge: List[str] = []  # Key facts from each round
        urls_seen: set = set()

        logger.info("Research agent starting: %r (max %d rounds)", question[:80], max_rounds)

        # ── Round 1: Generate initial search queries ──
        query_prompt = (
            f"You are a research assistant. Generate 2-3 search queries to thoroughly "
            f"research this question. Return ONLY a JSON array of query strings.\n\n"
            f"Question: {question}\n"
        )
        if context:
            query_prompt += f"Context: {context}\n"
        query_prompt += '\nExample: ["query 1", "query 2", "query 3"]'

        queries_text, cost = self._model_call(
            "You generate search queries. Output ONLY a JSON array of strings.",
            query_prompt,
            max_tokens=200,
        )
        total_cost += cost
        queries = self._parse_json_list(queries_text)
        if not queries:
            queries = [question]  # Fallback: use question directly

        # ── Research loop ──
        for round_num in range(max_rounds):
            if total_cost >= RESEARCH_COST_CAP:
                logger.info("Research cost cap reached ($%.4f), stopping", total_cost)
                break
            if len(all_sources) >= max_sources:
                logger.info("Source cap reached (%d), stopping", len(all_sources))
                break

            logger.info("Research round %d: %d queries", round_num + 1, len(queries))

            # Search
            round_results = []
            for q in queries[:3]:  # Max 3 queries per round
                results = self._search(q, max_results=5)
                for r in results:
                    if r["url"] and r["url"] not in urls_seen:
                        urls_seen.add(r["url"])
                        round_results.append(r)

            if not round_results:
                logger.info("Research round %d: no new results, stopping", round_num + 1)
                break

            # Extract full content from top results (if Tavily available)
            top_urls = [r["url"] for r in round_results[:MAX_EXTRACT_PER_ROUND]]
            extracted = self._extract_pages(top_urls)

            # Merge extracted content with search snippets
            for r in round_results:
                if r["url"] in extracted:
                    r["content"] = extracted[r["url"]]

            # Build round context for evaluation
            round_context = self._format_round_results(round_results[:8])

            # Evaluate: what did we learn? What's still missing?
            eval_prompt = (
                f"Research question: {question}\n\n"
                f"What we already know:\n{chr(10).join(accumulated_knowledge) or '(nothing yet)'}\n\n"
                f"New search results:\n{round_context}\n\n"
                f"Analyze these results. Return JSON with:\n"
                f'{{"key_facts": ["fact 1", "fact 2", ...], '
                f'"gaps": ["what is still unclear 1", ...], '
                f'"sufficient": true/false, '
                f'"follow_up_queries": ["query if more research needed", ...], '
                f'"source_assessments": [{{"url": "...", "title": "...", "relevance": 0.0-1.0, "summary": "1 sentence"}}]}}'
            )

            eval_text, cost = self._model_call(
                "You evaluate research results. Output ONLY valid JSON.",
                eval_prompt,
                max_tokens=600,
            )
            total_cost += cost

            eval_data = self._parse_json_obj(eval_text)

            # Record sources
            for sa in eval_data.get("source_assessments", []):
                all_sources.append(Source(
                    url=sa.get("url", ""),
                    title=sa.get("title", ""),
                    content_summary=sa.get("summary", ""),
                    relevance=sa.get("relevance", 0.5),
                ))

            # Accumulate knowledge
            for fact in eval_data.get("key_facts", []):
                if fact and fact not in accumulated_knowledge:
                    accumulated_knowledge.append(fact)

            # Check if we have enough
            if eval_data.get("sufficient", False):
                logger.info("Research round %d: sufficient info gathered", round_num + 1)
                break

            # Generate follow-up queries for next round
            queries = eval_data.get("follow_up_queries", [])
            if not queries:
                logger.info("Research round %d: no follow-up queries, stopping", round_num + 1)
                break

        # ── Synthesis ──
        search_rounds = min(round_num + 1, max_rounds) if 'round_num' in dir() else 1

        synth_prompt = (
            f"Research question: {question}\n\n"
            f"Context: {context or '(none)'}\n\n"
            f"Accumulated facts from {search_rounds} rounds of research:\n"
        )
        for i, fact in enumerate(accumulated_knowledge, 1):
            synth_prompt += f"{i}. {fact}\n"
        synth_prompt += (
            f"\nSources consulted: {len(all_sources)}\n\n"
            f"Synthesize these findings into a comprehensive answer. Return JSON:\n"
            f'{{"conclusion": "2-3 paragraph synthesis", '
            f'"key_findings": ["most important finding 1", ...], '
            f'"confidence": 0.0-1.0, '
            f'"gaps": ["what we couldn\'t determine", ...], '
            f'"recommendation": "clear recommendation if applicable, else empty string"}}'
        )

        synth_text, cost = self._model_call(
            "You synthesize research findings into clear conclusions. Output ONLY valid JSON.",
            synth_prompt,
            max_tokens=1000,
        )
        total_cost += cost

        synth_data = self._parse_json_obj(synth_text)

        elapsed = time.monotonic() - start
        result = ResearchResult(
            question=question,
            conclusion=synth_data.get("conclusion", "Research completed but synthesis failed."),
            key_findings=synth_data.get("key_findings", accumulated_knowledge[:8]),
            sources=sorted(all_sources, key=lambda s: s.relevance, reverse=True)[:max_sources],
            confidence=synth_data.get("confidence", 0.5),
            gaps=synth_data.get("gaps", []),
            recommendation=synth_data.get("recommendation", ""),
            search_rounds=search_rounds,
            total_cost=total_cost,
            elapsed_seconds=elapsed,
        )

        logger.info(
            "Research complete: %r — %d sources, %d rounds, $%.4f, %.1fs, confidence=%.0f%%",
            question[:60], len(result.sources), result.search_rounds,
            result.total_cost, elapsed, result.confidence * 100,
        )
        return result

    def compare_options(
        self,
        question: str,
        options: List[str],
        criteria: List[str],
        context: str = "",
    ) -> ResearchResult:
        """Research and compare multiple options against criteria.

        Args:
            question: The comparison question.
            options: List of options to compare (e.g. ["Suno API", "Udio API"]).
            criteria: Evaluation criteria (e.g. ["price", "quality", "reliability"]).
            context: Background constraints.

        Returns:
            ResearchResult with comparison in conclusion.
        """
        # Build a research question that covers all options
        enhanced_question = (
            f"{question}\n\nCompare these options: {', '.join(options)}\n"
            f"Evaluation criteria: {', '.join(criteria)}\n"
            f"For each option, find: pricing, features, reliability, documentation quality, "
            f"and any relevant user experiences or reviews."
        )
        return self.research(
            question=enhanced_question,
            context=context,
            max_rounds=min(len(options) + 2, MAX_SEARCH_ROUNDS),
        )

    # ── JSON parsing helpers ──

    @staticmethod
    def _parse_json_list(text: str) -> List[str]:
        """Extract a JSON list of strings from model output."""
        text = text.strip()
        # Find JSON array in text
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                if isinstance(data, list):
                    return [str(item) for item in data if item]
            except json.JSONDecodeError:
                pass
        # Fallback: split by newlines
        lines = [line.strip().strip("-•*").strip() for line in text.split("\n") if line.strip()]
        return [line for line in lines if len(line) > 5][:5]

    @staticmethod
    def _parse_json_obj(text: str) -> Dict[str, Any]:
        """Extract a JSON object from model output."""
        text = text.strip()
        # Find JSON object in text
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {}

    @staticmethod
    def _format_round_results(results: List[Dict[str, str]]) -> str:
        """Format search results for model evaluation."""
        parts = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            content = r.get("content", "")[:800]
            parts.append(f"[{i}] {title}\n    URL: {url}\n    {content}\n")
        return "\n".join(parts)
