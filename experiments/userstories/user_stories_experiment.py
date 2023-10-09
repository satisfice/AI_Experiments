import re
import json

import os
import openai
import json
import time
import datetime
import subprocess
import pymongo
from pymongo import MongoClient

openai.api_key = os.getenv("OPENAI_API_KEY")

client = MongoClient("localhost", 27017)
db = client.openai
col = db["log"]

def chat(version, mongo_collection, mess, parameters):
    result = ""
    while True:
        try:
            print("Calling API: model={}, temp={}, tokens={}".format(parameters["modl"],parameters["temp"],parameters["toke"]))
            result = openai.ChatCompletion.create(
                model=parameters["modl"],
                messages=mess,
                temperature=parameters["temp"],
                max_tokens=parameters["toke"],
                top_p=parameters["top_p"],
                frequency_penalty=parameters["freq"],
                presence_penalty=parameters["pres"]
                )
            break
        except:
            print("CALL FAILED, RETRYING IN 30 SEC...")
            time.sleep(30)
            pass
            
    mongo_collection.insert_one(
        {   
            "experiment": __file__,
            "version": version,
            "time": str(datetime.datetime.now()),
            "messages": mess,
            "model": parameters["modl"],
            "temperature": parameters["temp"],
            "max_tokens": parameters["toke"],
            "top_p": parameters["top_p"],
            "frequency_penalty": parameters["freq"],
            "presence_penalty": parameters["pres"],
            "result": result
        }
    )
    return result

def getfilename(base,localname):
    # split the base name into name and extension
    name, ext = os.path.splitext(base)
    # initialize the counter
    count = 1
    # loop until a unique filename is found
    while True:
        # if count is zero, use the base name as it is
        filename = f"{name}-{localname}-result{count}.json"
        front, back = os.path.split(filename)
        filename = front + "\\data\\" + back
        # check if the filename exists in the current directory
        if os.path.exists(filename):
            # if yes, increment the count and try again
            count += 1
            # if no, return the filename
        else:
            return filename    

version = subprocess.check_output(['git','log','-n','1','--format="%H"',__file__]).decode("utf-8")
version = version[1:-2]

###################################################################
# Experiment
###################################################################

blocks = [
#    {
#        "name": "T00M40",
#        "temp": 0,
#        "modl": "gpt-4",
#    },
    {
        "name": "T03M40",
        "temp": .3,
        "modl": "gpt-4",
    },
#    {
#        "name": "T08M40",
#        "temp": .8,
#        "modl": "gpt-4",
#    },
#    {
#        "name": "T00M35",
#        "temp": 0,
#        "modl": "gpt-3.5-turbo",
#    },
#    {
#        "name": "T03M35",
#        "temp": .3,
#        "modl": "gpt-3.5-turbo",
#    },
#    {
#        "name": "T08M35",
#        "temp": .8,
#        "modl": "gpt-3.5-turbo",
#    }
]

# Perform experiment
def experiment(version, mongo_collection, parameters, us_data):
    outcomes = []

    for story in us_data:
        
# Retry until success        
        while True:
            res = chat(version, mongo_collection, [
            {
              "role": "user",
              "content": "You are a test analyst trying to understand a user story for a social media product. Your task is to identify and list every testable element of the software product that is stated or implied by this user story. You must present your output in JSON form without any commentary. For instance, if the user story mentions sending a notification, then \"notifications\" should be included in the JSON. The JSON should have a simple array format under a single key called \"testableElement\". Do not use names of people in your output. Instead write \"user\" or \"user1\" if there are more than one user mentioned.\n\nThe story you must analyze is titled: " + story["title"] + "\n\n" + "Here is the story:\n" + story["body"]
            }
            ],parameters)

            try:
                answer = json.loads(res["choices"][0]["message"]["content"])
                break
            except:
                pass
        outcomes.append(story)
        outcomes.append(answer)
    return outcomes   


us_data = []
with open("user_stories.json","rt",encoding="UTF8") as f:
    us_data = json.load(f)
    
# Run with different ChatGPT parameters 
for block in blocks:
    print("==============")
    print(block)
    parameters = {
        "temp": block["temp"],
        "modl": block["modl"],
        "toke": 2048,
        "top_p": 1,
        "freq": 0,
        "pres": 0
    }
    
    filename = getfilename(__file__, block["name"])
    answers = [
        {
            "experiment": __file__,
            "version": version,
            "time": str(datetime.datetime.now()),
            "model": parameters["modl"],
            "temperature": parameters["temp"],
            "tokens": parameters["toke"],
            "top": parameters["top_p"],
            "freq": parameters["freq"],
            "pres": parameters["pres"]
        }
    ]

# Run trials    
    for trial in range(0,1):
        print(trial+1)
        answers.append(experiment(version, col, parameters, us_data))
        with open(filename,"wt") as f:
            print(json.dumps(answers,indent=2),file=f)
