
sentence_templates = [
    "{np1} are better than {np2}.",
    "{np1} are more important than {np2}.",
    "{np1} are less effective than {np2}.",
    "{np1} are equally important as {np2}.",
    "Certainly, {np1} are pretty cool."
]
noun_phrase_templates = [
    "{np1} without {np2}",
    "{np1} with {np2}",
    "{np1} that have {np2}",
    "{adj1} and {adj2} {np1}",
    "{np1} that are {adj1}"
]

nouns = [
    "cats",
    "dogs",
    "birds",
    "fish",
    "lizards",
    "hamsters"
]

adjectives = [
    "big",
    "small",
    "colorful",
    "fast",
    "slow",
    "playful"
]

import random
import sys

def generate_np(level=0):
    if level == 0:
        n1, n2 = random.sample(nouns, 2)
        np1 = {"phrase": n1, "subphrases": []}
        np2 = {"phrase": n2, "subphrases": []}
    else:
        np1 = generate_np(level - 1)
        np2 = generate_np(level - 1)
    
    adj1, adj2 = random.sample(adjectives, 2)
    template = random.choice(noun_phrase_templates)
    phrase = {"phrase": template.format(np1=np1["phrase"], np2=np2["phrase"], adj1=adj1, adj2=adj2), "subphrases": []}
    if np1["phrase"] in phrase["phrase"]:
        phrase["subphrases"].extend(np1["subphrases"])
        phrase["subphrases"].append(np1["phrase"])
    if np2["phrase"] in phrase["phrase"]:
        phrase["subphrases"].extend(np2["subphrases"])
        phrase["subphrases"].append(np2["phrase"])
    return phrase

def generate_sentence(level):
    np1 = generate_np(level)
    np2 = generate_np(level)
    template = random.choice(sentence_templates)
    sentence = {"phrase": template.format(np1=np1["phrase"], np2=np2["phrase"]), "subphrases": []}
    if np1["phrase"] in sentence["phrase"]:
        sentence["subphrases"].extend(np1["subphrases"])
        sentence["subphrases"].append(np1["phrase"])
    if np2["phrase"] in sentence["phrase"]:
        sentence["subphrases"].extend(np2["subphrases"])
        sentence["subphrases"].append(np2["phrase"])
    return sentence

text = ""
generated_phrases = set()
print("Generated Sentences:")
for _ in range(10):
    s = generate_sentence(1)
    sentence = s["phrase"][0].upper() + s["phrase"][1:]
    text += sentence + " "
    generated_phrases.update(s["subphrases"])
print(text)
print()
print("Generated Noun Phrases:")
for phrase in sorted(generated_phrases):
    if phrase in text.lower():
        print(phrase)
    else:
        print(f"* {phrase} *")