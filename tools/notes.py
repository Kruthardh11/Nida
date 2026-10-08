# tools/notes.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Memory / Notes Tool
#
# Provides Nida with long-term memory across sessions using plain Markdown files.
# Nida can track roadmaps, checklists, or concept summaries here.
#
# ACTIONS:
#   list   — list all existing memory files
#   read   — read contents of a specific file
#   write  — create or entirely overwrite a file
#   append — add text to the bottom of a file
# ─────────────────────────────────────────────────────────────────────────────

import logging
from pathlib import Path

from tools.base import BaseTool, ToolResult

logger = logging.getLogger("nida.notes")

MEMORY_DIR = Path(__file__).parent.parent / "memory"

class NotesTool(BaseTool):
    """
    Manages persistent memory by reading and writing local Markdown files.
    Use this to save roadmaps, daily schedules, and study notes.
    """

    @property
    def name(self) -> str:
        return "notes"

    @property
    def description(self) -> str:
        return "Read/write notes to maintain long-term memory across sessions."

    @property
    def args_schema(self) -> dict[str, str]:
        return {
            "action": "list, read, write, append, checkoff, or delete",
            "file":   "e.g., 'dsa_roadmap.md' (not required for 'list')",
            "content": "Text to write/append, OR the exact topic string to check off if action=checkoff"
        }

    def run(self, args: dict) -> ToolResult:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        
        action = args.get("action", "").strip().lower()

        if action == "list":
            return self._list_files()

        filename = args.get("file", "").strip()
        if not filename:
            return ToolResult(success=False, error="File name is required for read, write, append, checkoff, or delete.")
            
        # Prevent path traversal
        if "/" in filename or "\\" in filename or ".." in filename:
            return ToolResult(success=False, error="Invalid filename. Use plain names like 'notes.md'.")
            
        filepath = MEMORY_DIR / filename

        if action == "read":
            return self._read_file(filepath)
        elif action == "write":
            content = args.get("content", "")
            return self._write_file(filepath, content, append=False)
        elif action == "append":
            content = args.get("content", "")
            return self._write_file(filepath, content, append=True)
        elif action == "delete":
            return self._delete_file(filepath)
        elif action == "checkoff":
            content = args.get("content", "")
            return self._checkoff_item(filepath, content)
        else:
            return ToolResult(success=False, error=f"Unknown action: '{action}'.")

    def _list_files(self) -> ToolResult:
        files = [f.name for f in MEMORY_DIR.iterdir() if f.is_file()]
        if not files:
            return ToolResult(success=True, output="Memory is empty. No files exist.")
        return ToolResult(success=True, output="Existing memory files: " + ", ".join(files))

    def _read_file(self, path: Path) -> ToolResult:
        if not path.exists():
            return ToolResult(success=False, error=f"File '{path.name}' does not exist.")
        
        try:
            content = path.read_text(encoding="utf-8")
            logger.info(f"Read memory: {path.name}")
            return ToolResult(success=True, output=f"Contents of {path.name}:\n\n{content}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _write_file(self, path: Path, content: str, append: bool) -> ToolResult:
        try:
            mode = "a" if append else "w"
            if append and path.exists() and content:
                if not content.startswith("\n"):
                    content = "\n" + content

            with open(path, mode, encoding="utf-8") as f:
                f.write(content)

            verb = "Appended to" if append else "Created/Overwrote"
            logger.info(f"{verb} memory: {path.name}")
            return ToolResult(success=True, output=f"{verb} {path.name} successfully.")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _delete_file(self, path: Path) -> ToolResult:
        if not path.exists():
            return ToolResult(success=False, error=f"File '{path.name}' does not exist.")
        try:
            path.unlink()
            logger.info(f"Deleted memory: {path.name}")
            return ToolResult(success=True, output=f"Deleted {path.name} successfully.")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _checkoff_item(self, path: Path, topic: str) -> ToolResult:
        if not path.exists():
            return ToolResult(success=False, error=f"File '{path.name}' does not exist.")
        if not topic:
            return ToolResult(success=False, error="Topic string is required to checkoff an item.")
            
        try:
            content = path.read_text(encoding="utf-8")
            lines = content.split('\n')
            found = False
            for i, line in enumerate(lines):
                if topic.lower() in line.lower() and "[ ]" in line:
                    lines[i] = line.replace("[ ]", "[x]")
                    found = True
                    break
                    
            if not found:
                return ToolResult(success=False, error=f"Could not find an unchecked topic matching '{topic}' in {path.name}.")
                
            path.write_text('\n'.join(lines), encoding="utf-8")
            logger.info(f"Checked off item in memory: {path.name}")
            return ToolResult(success=True, output=f"Successfully checked off '{topic}' in {path.name}.")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
