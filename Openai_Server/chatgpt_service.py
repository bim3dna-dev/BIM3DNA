import os
import sys
from openai import OpenAI
from config import OPENAI_API_KEY

# Load API key from environment
# OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY
client = OpenAI(api_key=OPENAI_API_KEY)

# Set your OpenAI API key. It first checks the environment variable,
# and if it's not set, it uses the value from config.py.
# openai.api_key = os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY
# openai.api_key = os.getenv("OPENAI_API_KEY")


def ask_openai(
    question,
    model="gpt-4o",  # 'o' as in omni, fastest & cheapest GPT-4 model
    max_tokens=300,
    temperature=0.5,
    top_p=1.0,
    frequency_penalty=0.0,
    presence_penalty=0.0,
):
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": question}],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return "Error: " + str(e)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Error: No question provided.")
        sys.exit(1)
    question = sys.argv[1]
    print(ask_openai(question))
