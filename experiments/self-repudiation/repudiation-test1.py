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
    {
        "name": "T03M35",
        "temp": .3,
        "modl": "gpt-3.5-turbo",
    },
    {
        "name": "T08M35",
        "temp": .8,
        "modl": "gpt-3.5-turbo",
    },
    {
        "name": "T13M35",
        "temp": 1.3,
        "modl": "gpt-3.5-turbo",
    },
    {
        "name": "T03M40",
        "temp": .3,
        "modl": "gpt-4",
    },
    {
        "name": "T08M40",
        "temp": .8,
        "modl": "gpt-4",
    },
    {
        "name": "T13M40",
        "temp": 1.3,
        "modl": "gpt-4",
    }
]


# Perform experiment
def experiment(version, mongo_collection, parameters):
    mess = [
        {
          "role": "system",
          "content": """You are a testing expert.

    Requirement (written by the project manager)

    We are implementing an account renewal reminder feature in an existing account management system.

    Background
    - An account expires three years to the day after it is opened.
    - There is a grace period of six months before the expired account is closed and the account information deleted.
    - Three months before the account information is deleted, we want account holders to be reminded that their expired account will be closed and deleted at the end of the grace period, and offer them the opportunity to reactivate the account before that happens.
    - The reminder message will be sent out on the reminder date, and will contain the expiry date and the date on which the grace period ends.
    - The calculation of the expiry date and the end of the grace period has already been thoroughly tested."""
        },
        {
          "role": "user",
          "content": "Analyze this spec for completeness."
        }  
    ]
    
    final_output = []
    result = chat(version, mongo_collection, mess, parameters)
    output = {
                "prompt": mess.copy(),
                "answer": result
             }
    final_output.append(output)
    
    mess.append(result.choices[0].message)
    mess.append({
      "role": "user",
      "content": "Are you sure this is right?"
    })
    result = chat(version, mongo_collection, mess, parameters)
    output = {
                "prompt": mess.copy(),
                "answer": result
             }
    final_output.append(output)
    return final_output

# Run with different ChatGPT parameters 
for block in blocks:
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
    
    for trial in range(0,10):
        print(trial+1)
        answers.append(experiment(version, col, parameters))
        time.sleep(5)
        with open(filename,"wt") as f:
            print(json.dumps(answers,indent=2),file=f)

