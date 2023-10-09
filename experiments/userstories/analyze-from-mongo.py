import json
import re

with open("openai.log.json","rt",encoding="UTF8") as f:
    data = json.load(f)
    
master = {}
for rec in data:
    prompt = rec["messages"][0]["content"]
    result = rec["result"]["choices"][0]["message"]["content"]
    title = re.search("titled: (.*?)$",prompt,re.MULTILINE).group(1)
    print(title)
    print(prompt)
    print(result)
    
    result = json.loads(result)
    print(result.keys())
    for testable in result[list(result.keys())[0]]:
        if not testable in master:
            master[testable] = [title]
        else:
            master[testable].append(title)
           
print(json.dumps(sorted(master),indent=2))