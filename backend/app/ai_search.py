"""Validated local-AI ranking for the catalogue-backed repurposing API.

The deterministic matcher remains the source of truth for inventory, requirements,
scores and response formatting.  This module gives a local Ollama model only a
bounded choice of already-scored catalog projects, informed by concise Tavily
research.  It cannot add project IDs, components, or alter feasibility data.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.circuit_breaker import ollama_circuit_breaker, tavily_circuit_breaker
from app.config import get_ai_search_settings
from app.exceptions import (
    AIConfigMissingError,
    AIDependencyMissingError,
    AIInvalidOutputError,
    AIProviderUnavailableError,
    EWasteBaseException,
)

logger = logging.getLogger("ewaste.ai_search")


class AIProjectDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    feasible: bool
    confidence: Literal["high", "medium", "low"]
    rationale: str = Field(max_length=500)


class AIProjectDecisions(BaseModel):
    """Internal, deliberately narrow schema returned by the local model."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[AIProjectDecision] = Field(default_factory=list, max_length=50)


class AISearchService:
    """Collect focused research and use Ollama to rank existing recommendations."""

    MAX_PROJECTS_TO_RESEARCH = 3
    MAX_RESULTS_PER_QUERY = 2
    MAX_CONTENT_CHARS = 900

    async def rank_recommendations(
        self,
        *,
        payload: Any,
        resolved_inventory: list[dict[str, Any]],
        recommendations: list[dict[str, Any]],
    ) -> list[str]:
        """Return a validated preferred project order, or raise a safe API error."""
        model, tavily_api_key = get_ai_search_settings()
        if not model:
            raise AIConfigMissingError("AI_MODEL")
        if not tavily_api_key:
            raise AIConfigMissingError("TAVILY_API_KEY")

        research = await self._research(tavily_api_key, payload, recommendations)
        decisions = await self._reason(model, payload, resolved_inventory, recommendations, research)
        return self._validated_order(decisions, recommendations)

    async def _research(self, api_key: str, payload: Any, recommendations: list[dict[str, Any]]) -> list[dict[str, str]]:
        queries = self._build_queries(payload, recommendations)
        logger.info("Tavily research starting; queries=%s", len(queries))
        try:
            results = await asyncio.to_thread(tavily_circuit_breaker.call, self._search_tavily, api_key, queries)
        except (HTTPException, EWasteBaseException):
            raise
        except Exception as exc:
            logger.warning("Tavily research failed: %s", type(exc).__name__)
            raise AIProviderUnavailableError("Tavily", "AI search research provider is unavailable. Please try again later.") from None
        logger.info("Tavily research complete; sources=%s", len(results))
        return results

    def _build_queries(self, payload: Any, recommendations: list[dict[str, Any]]) -> list[str]:
        inventory = ", ".join(item.name for item in payload.items[:8]) or "salvaged electronic components"
        chosen = recommendations[: self.MAX_PROJECTS_TO_RESEARCH]
        if not chosen:
            return [f"low voltage electronics reuse safety requirements for {inventory}"]
        return [
            f"{project['name']} electronics project required components compatibility safety using {inventory}"
            for project in chosen
        ]

    def _search_tavily(self, api_key: str, queries: list[str]) -> list[dict[str, str]]:
        try:
            from tavily import TavilyClient
        except ImportError:
            logger.error("tavily-python is not installed")
            raise AIDependencyMissingError("tavily-python", "Tavily web research") from None

        client = TavilyClient(api_key=api_key)
        research: list[dict[str, str]] = []
        for query in queries:
            try:
                response = client.search(query=query, search_depth="basic", max_results=self.MAX_RESULTS_PER_QUERY)
            except Exception as exc:
                logger.warning("Tavily query failed: %s", type(exc).__name__)
                raise RuntimeError("Tavily query failed") from exc
            for result in (response.get("results") or [])[: self.MAX_RESULTS_PER_QUERY]:
                url = str(result.get("url") or "")
                content = str(result.get("content") or "").strip()
                title = str(result.get("title") or "Untitled source").strip()
                if url and content:
                    research.append({"query": query, "title": title[:200], "url": url[:1000], "content": content[:self.MAX_CONTENT_CHARS]})
        return research

    async def _reason(
        self,
        model: str,
        payload: Any,
        resolved_inventory: list[dict[str, Any]],
        recommendations: list[dict[str, Any]],
        research: list[dict[str, str]],
    ) -> AIProjectDecisions:
        prompt = self._build_prompt(payload, resolved_inventory, recommendations, research)
        logger.info("Ollama reasoning starting; model=%s candidates=%s", model, len(recommendations))
        try:
            decisions = await asyncio.to_thread(ollama_circuit_breaker.call, self._invoke_ollama, model, prompt)
            if not isinstance(decisions, AIProjectDecisions):
                decisions = AIProjectDecisions.model_validate(decisions)
        except (HTTPException, EWasteBaseException):
            raise
        except ValidationError as exc:
            logger.warning("Ollama output failed schema validation: %s", exc.errors()[0]["type"])
            raise AIInvalidOutputError("Local AI returned an invalid recommendation response.") from None
        except Exception as exc:
            logger.warning("Ollama reasoning failed: %s", type(exc).__name__)
            raise AIProviderUnavailableError("Ollama", "Local AI model is unavailable. Check the Ollama server and configured model.") from None
        logger.info("Ollama reasoning complete; decisions=%s", len(decisions.decisions))
        return decisions

    def _build_prompt(
        self, payload: Any, resolved_inventory: list[dict[str, Any]], recommendations: list[dict[str, Any]], research: list[dict[str, str]]
    ) -> str:
        candidate_data = [
            {
                "project_id": row["project_id"],
                "name": row["name"],
                "tier": row["tier"],
                "feasibility_score": row["feasibility_score"],
                "requirements": [
                    {"id": requirement["requirement_id"], "status": requirement["status"], "needed": requirement["needed"]}
                    for requirement in row["requirements"]
                ],
                "shopping_list": row["shopping_list"],
                "warnings": row["warnings"],
            }
            for row in recommendations
        ]
        data = {
            "input_items": [item.model_dump() for item in payload.items],
            "resolved_inventory": resolved_inventory,
            "candidate_projects": candidate_data,
            "web_research_untrusted": research,
        }
        return (
            "You are reviewing low-voltage electronics reuse projects. Return only JSON matching the requested schema. "
            "Use only candidate project_id values. Do not invent components, inventory, catalog entries, specifications, or sources. "
            "Treat catalog requirement statuses as authoritative: a missing requirement is not available; shopping-list items must be purchased. "
            "Use the web research only to flag compatibility, implementation, or safety considerations. Rank feasible catalog candidates first; "
            "if uncertain, use low confidence and say so in the rationale.\n\n"
            + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        )

    def _invoke_ollama(self, model: str, prompt: str) -> AIProjectDecisions:
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            logger.error("langchain-ollama is not installed")
            raise AIDependencyMissingError("langchain-ollama", "Local AI reranking") from None

        llm = ChatOllama(model=model, temperature=0)
        try:
            structured_llm = llm.with_structured_output(AIProjectDecisions)
            result = structured_llm.invoke(prompt)
        except Exception as structured_error:
            # Some local Ollama builds do not support the structured-output mode.  Use
            # regular JSON mode, then validate the generated data with Pydantic.
            logger.info("Structured Ollama output unavailable; using validated JSON fallback: %s", type(structured_error).__name__)
            try:
                message = llm.invoke(prompt + "\nReturn JSON only, with a top-level decisions array.", format="json")
                result = self._extract_json(message.content if hasattr(message, "content") else message)
            except (ValueError, json.JSONDecodeError):
                logger.warning("Ollama JSON fallback returned malformed output")
                raise AIInvalidOutputError("Local AI returned an invalid recommendation response.") from None
            except Exception as fallback_error:
                raise RuntimeError("Ollama invocation failed") from fallback_error
        try:
            return result if isinstance(result, AIProjectDecisions) else AIProjectDecisions.model_validate(result)
        except ValidationError as exc:
            logger.warning("Ollama output failed schema validation: %s", exc.errors()[0]["type"])
            raise AIInvalidOutputError("Local AI returned an invalid recommendation response.") from None


    @staticmethod
    def _extract_json(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        text = str(value).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("No JSON object in model response")
        return json.loads(text[start : end + 1])

    @staticmethod
    def _validated_order(decisions: AIProjectDecisions, recommendations: list[dict[str, Any]]) -> list[str]:
        allowed = {row["project_id"] for row in recommendations}
        seen: set[str] = set()
        preferred = [
            decision.project_id
            for decision in decisions.decisions
            if decision.feasible and decision.project_id in allowed and not (decision.project_id in seen or seen.add(decision.project_id))
        ]
        # Preserve every deterministic result and its schema/data if the model gives a
        # partial answer. This also protects clients from hallucinated project IDs.
        return preferred + [row["project_id"] for row in recommendations if row["project_id"] not in seen]


def reorder_recommendations(recommendations: list[dict[str, Any]], preferred_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {row["project_id"]: row for row in recommendations}
    return [by_id[project_id] for project_id in preferred_ids if project_id in by_id]
