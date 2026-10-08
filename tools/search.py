# tools/search.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Web Search Tool
#
# Provides DuckDuckGo integration for real-time web search.
# Completely local, free, and requires no API keys!
#
# ACTIONS:
#   search — Get top text summaries for a given query.
# ─────────────────────────────────────────────────────────────────────────────

import logging
from ddgs import DDGS

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.search")

class SearchTool(BaseTool):
    """
    Fetches real-time information from the web across the internet.
    Particularly useful for Learn Mode (e.g., LeetCode problems, tech concepts).
    """

    @property
    def name(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return "Search the web for information (e.g., tech concepts, problem statements)"

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "query": "The search term to look up.",
            "max_results": "(optional) number of top results to return, default is 3"
        }

    def run(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query:
            return ToolResult(success=False, error="Search query is required.")

        try:
            max_results = int(args.get("max_results", 3))
        except (ValueError, TypeError):
            max_results = 3

        logger.info(f"Searching web for: {query}")
        
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            
            if not results:
                return ToolResult(success=True, output="No results found for that query.")

            output_lines = []
            for i, res in enumerate(results, 1):
                title = res.get('title', 'No Title')
                body = res.get('body', 'No Body Text')
                output_lines.append(f"{i}. {title}: {body}")
            
            # Join beautifully for the LLM to read
            content = " ".join(output_lines)
            logger.info(f"Found {len(results)} search results.")
            return ToolResult(success=True, output=f"Found {len(results)} results: {content}")
            
        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return ToolResult(success=False, error=str(e))
