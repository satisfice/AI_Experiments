import json
import re
import sys

filename = sys.argv[1]

with open(filename,"rt",encoding="UTF8") as f:
    data = json.load(f)
   

master = {}
for rec in range(0,len(data[1])-1,2):
    title = data[1][rec]["title"]
    result = data[1][rec+1]
    
    for testable in result[list(result.keys())[0]]:
        if not testable in master:
            master[testable] = [title]
        else:
            master[testable].append(title)
print(json.dumps(master,indent=2))           
print(json.dumps(sorted(master),indent=2))