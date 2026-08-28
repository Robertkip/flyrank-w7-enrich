"""Stage 0 throwaway — prove a model answers, from this machine.

    python -m src.llm.hello

Three environment variables are the entire difference between a model running on
this laptop and one running in a datacentre. That is why nothing here hard-codes a
provider, and why the rest of this project never does either.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.environ["LLM_BASE_URL"],  # Ollama: http://localhost:11434/v1/
    api_key=os.environ["LLM_API_KEY"],  # Ollama: the literal string "ollama"
    timeout=30.0,
)

res = client.chat.completions.create(
    model=os.environ["LLM_MODEL"],
    messages=[{"role": "user", "content": "Reply with exactly the word: ready"}],
)
print(res.choices[0].message.content)
