import sys
import json
import re
import inflect  # Make sure to install: pip install inflect

# Inflect engine for singular/plural normalization
p = inflect.engine()


def normalize(text):
    """Convert plural nouns to singular (e.g., windows → window)"""
    return p.singular_noun(text) or text


def filter_elements(question, model_data):
    """
    Filters model_data based on keywords found in the question.
    Matches are normalized to singular form for robustness.
    """

    q = question.lower()
    words_in_question = re.findall(r"\w+", q)  # Extract clean words
    normalized_words = [normalize(word) for word in words_in_question]

    # Define keyword mappings (expandable)
    category_keywords = {
        "wall": ["wall", "partition"],
        "door": ["door", "entry", "exit"],
        "window": ["window", "fenestration", "opening"],
        "floor": ["floor", "level", "story"],
        "roof": ["roof", "covering"],
        "column": ["column", "pillar"],
        "beam": ["beam", "girder"],
        "stair": ["stair", "step", "staircase"],
        "furniture": ["furniture", "chair", "table", "sofa", "desk"],
        "fixture": ["fixture", "lighting", "lamp"],
        "plumbing": ["pipe", "plumbing", "water"],
        "electrical": ["electrical", "conduit", "cable", "wiring"],
        "hvac": ["hvac", "air", "vent", "duct"],
        "structural": ["structural", "steel", "concrete", "reinforcement"],
        "exterior": ["exterior", "facade", "cladding"],
        "interior": ["interior"],
        "landscape": ["landscape", "garden", "tree", "plant"],
    }

    matched_categories = []

    for category, keywords in category_keywords.items():
        for word in normalized_words:
            if word in keywords:
                matched_categories.append(category)
                break

    matched_categories = list(set(matched_categories))  # Remove duplicates

    # Debugging print
    # print("Matched categories:", matched_categories)

    if matched_categories:
        filtered_elements = []
        seen_ids = set()
        for element in model_data:
            cat_str = element.get("Category", "").lower()
            for cat in matched_categories:
                if cat in cat_str:
                    eid = element.get("Id")
                    if eid not in seen_ids:
                        seen_ids.add(eid)
                        filtered_elements.append(element)
        return filtered_elements
    else:
        return model_data  # fallback if no match


if __name__ == "__main__":
    input_data = sys.stdin.read()
    try:
        data = json.loads(input_data)
        question = data.get("question", "")
        model_data = data.get("model_data", [])

        matching_elements = filter_elements(question, model_data)
        count = len(matching_elements)

        response = "Received question: {0}. Number of matching elements: {1}.".format(
            question, count
        )
        print(response)
    except Exception as e:
        print("Error: " + str(e))
