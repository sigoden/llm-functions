import sys
import re

def run(content: str):
    """Scan text content for potential Prompt Injection or Jailbreak patterns. 
    Use before processing untrusted external files/logs with the Agent.
    Args:
        content: The text string to analyze.
    """
    
    # 1. Direct command override patterns (English/Markdown focused)
    patterns = {
        "Direct Override": r"(ignore|disregard)\s+(all|your)\s+(previous|prior)\s+(instruction|command|direction)",
        "Role Hijack": r"(system|you are)\s+(now)?\s*(a|an|serving as)?\s*(uncensored|developer|root|admin)",
        "Delimiter Injection": r"(\n|^)\s*(SYSTEM|USER|ASSISTANT)\s*:",
        "Base64 Obfusc trick": r".{10}(VmlydHVhbDo|dmlydHVhbDo).{10}", # Common b64 fragment 
        "Suspicious Repetition": r"(.{1,10})\1{20,}" # 20x repetition of same pattern (Buffer overflows attempts)
    }

    alerts = []
    
    # Use lowercase for case-insensitive matching where simple
    content_lower = content.lower()

    for name, pattern in patterns.items():
        if re.search(pattern, content, re.IGNORECASE):
            alerts.append(name)

    # 2. Heuristic check: Excessive format shifting
    # If text is 90% non-ascii or looks like random machine code
    # (Simplified check: weird control chars usage)
    dangerous_controls = len(re.findall(r'[\x00-\x08\x0E-\x1F]', content))
    if dangerous_controls > 5:
        alerts.append("High Binary/Control Character Count used")

    if alerts:
        print(f"** SECURITY ALERT **: Detection Level HIGH")
        print(f"Malicious patterns detected in input:")
        for alert in alerts:
            print(f"- {alert}")
        print("\nReview Manually. Do not execute context blindly.")
    else:
        print("Status: Pass. No rigorous injection signatures detected.")
