import os
import weave
import json
from openai import OpenAI

print("OPENAI_API_KEY =", os.getenv("OPENAI_API_KEY"))


# Decorator to track the function's inputs and outputs
@weave.op()
def extract_fruit(sentence: str) -> dict:
    client = OpenAI()
    system_prompt = (
        "Parse sentences into a JSON dict with keys: fruit, color, and flavor."
    )
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",  # or another model you wish to test
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": sentence},
        ],
        temperature=0.7,
        max_tokens=50,
        # Adjust response_format if necessary for your test
    )
    # Assume the response contains a JSON-formatted string
    extracted = response.choices[0].message.content
    return json.loads(extracted)


# Initialize a new Weave project
weave.init("intro-example")

# Test function call
sentence = "There are many fruits found on planet Goocrux, including purple, candy-flavored neoskizzles."
result = extract_fruit(sentence)
print("Extracted data:", result)
