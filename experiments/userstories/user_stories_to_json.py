import re
import json

with open("Social Media User Stories.md","rt",encoding="UTF8") as f:
    data = f.readlines()

title = "<first>"
stories = []
story_lines = ""
for line in data:
    if re.search("^\s*$",line):
        continue
    if line.startswith("###"):
        if title != "<first>":
            stories.append({"title":title,"body":story_lines})
            story_lines = ""
        title = re.search("### \*\*(.*?)\*\*",line).group(1)
        continue
    story_lines += line
stories.append({"title":title,"body":story_lines})

print(json.dumps(stories,indent=2))
