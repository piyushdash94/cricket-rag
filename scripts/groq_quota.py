"""How much Groq daily quota is left? Sends one ~2.6k-token request per model; a 429 says when it frees."""
import httpx
from cricrag.config import settings
s = settings()
big = "cricket " * 2500
for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=60,
                   headers={"Authorization": f"Bearer {s.groq_api_key}"},
                   json={"model": m, "messages": [{"role": "user", "content": big + " Say ok."}], "max_tokens": 20})
    print(m, r.status_code, r.headers.get("retry-after"), r.text[:400])
