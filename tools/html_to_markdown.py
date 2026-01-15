import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

def run(url: str, selector: str = "body"):
    """Fetch a webpage and convert it to clean Markdown. Best for reading documentation or articles.
    Args:
        url: The URL to fetch.
        selector: CSS selector to extract (default: 'body', use 'main' or 'article' for better focus).
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; LLMAgent/1.0)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove junk elements that confuse LLMs
        for script in soup(["script", "style", "nav", "footer", "iframe", "noscript"]):
            script.decompose()

        # Select specific content if requested
        content = soup.select_one(selector) if selector != "body" else soup.body
        
        if not content:
            print("Error: content not found for selector")
            return

        # Convert to Markdown to save tokens
        clean_text = md(str(content), heading_style="ATX")
        
        # Remove excessive newlines
        print(clean_text.strip())

    except Exception as e:
        print(f"Error fetching URL: {e}")
