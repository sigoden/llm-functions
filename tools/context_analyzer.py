import re
from typing import List


class ContextAnalyzer:
    def __init__(self):
        self.tool_keywords = {
            "file": ["fs_read", "fs_write", "fs_ls", "fs_patch", "edit.py"],
            "web": ["web_search", "fetch_url_via_curl", "fetch_url_via_jina"],
            "code": ["execute_py_code", "execute_js_code", "execute_command"],
            "data": ["code_format_json", "text_count", "text_sort"],
            "system": ["sys_cpu_info", "sys_mem_info", "sys_disk_info"],
        }

    def analyze_context(self, message: str) -> List[str]:
        """Analyze message and suggest relevant tools."""
        message_lower = message.lower()
        suggested_tools = []

        # Check for keywords
        for category, tools in self.tool_keywords.items():
            if category in message_lower:
                suggested_tools.extend(tools)

        # Check for specific patterns
        if re.search(r"\.py|python|def |import ", message_lower):
            suggested_tools.extend(["execute_py_code"])

        if re.search(r"\.js|javascript|function|const ", message_lower):
            suggested_tools.extend(["execute_js_code"])

        if re.search(r"https?://|www\.|\.com", message_lower):
            suggested_tools.extend(["web_search", "fetch_url_via_curl"])

        if re.search(r"bybit|trade|balance|position", message_lower):
            suggested_tools.extend(
                ["bybit_get_balance", "bybit_get_positions", "bybit_get_ticker"]
            )

        # Remove duplicates and return
        return sorted(set(suggested_tools))


def run(message: str):
    """Analyze context and suggest tools.
    Args:
        message: User message or conversation context
    """
    analyzer = ContextAnalyzer()
    suggestions = analyzer.analyze_context(message)

    return {
        "message": message,
        "suggested_tools": suggestions,
        "confidence": min(1.0, len(suggestions) / 10.0),
    }
