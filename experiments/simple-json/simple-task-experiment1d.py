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
        "name": "T00M40",
        "temp": 0,
        "modl": "gpt-4",
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
        "name": "T00M35",
        "temp": 0,
        "modl": "gpt-3.5-turbo",
    },
    {
        "name": "T03M35",
        "temp": .3,
        "modl": "gpt-3.5-turbo",
    },
    {
        "name": "T08M35",
        "temp": .8,
        "modl": "gpt-3.5-turbo",
    }
]

words = [
    "https://js.arcgis.com/3.44/dojox/gfx/filters.js",
    "https://js.arcgis.com/3.44/esri/layers/VectorTileLayer.js",
    "https://js.arcgis.com/3.44/esri/layers/RasterXLayer.js",
    "https://js.arcgis.com/3.44/dojox/gfx/svg.js",
    "https://js.arcgis.com/3.44/esri/layers/vectorTiles/core/workers/worker.js",
    "https://js.arcgis.com/3.44/esri/dijit/BasemapToggle.js",
    "https://js.arcgis.com/3.44/esri/layers/vectorTiles/core/workers/worker-init.js",
    "https://js.arcgis.com/3.44/dojo/resources/blank.gif",
    "https://js.arcgis.com/3.44/esri/config.js",
    "https://js.arcgis.com/3.44/esri/nls/jsapi_en-us.js",
    "https://js.arcgis.com/3.44/esri/dijit/HomeButton.js",
    "https://js.arcgis.com/3.44/esri/layers/nls/RasterXLayer_en-us.js",
    "https://js.arcgis.com/3.44/esri/layers/VectorTileLayerImpl.js",
    "https://js.arcgis.com/3.44/esri/layers/RasterXLayer.js",
    "https://js.arcgis.com/3.44/esri/dijit/BasemapToggle.js",
    "https://js.arcgis.com/3.44/dojox/gfx/svgext.js",
    "https://js.arcgis.com/3.44/esri/layers/VectorTileLayer.js",
    "https://js.arcgis.com/3.44/esri/config.js",
    "https://js.arcgis.com/3.44/esri/layers/vectorTiles/core/workers/worker.js",
    "https://js.arcgis.com/3.44/esri/dijit/HomeButton.js",
    "https://js.arcgis.com/3.44/esri/layers/nls/VectorTileLayerImpl_en-us.js",
    "https://js.arcgis.com/3.44/esri/images/basemap/gray.jpg",
    "https://js.arcgis.com/3.44/esri/layers/support/webglDeps.js",
    "https://js.arcgis.com/3.44/esri/layers/RasterXLayer.js",
    "https://js.arcgis.com/3.44/dojox/gfx/svg.js",
    "https://js.arcgis.com/3.44/dojo/resources/blank.gif",
    "https://js.arcgis.com/3.44/esri/dijit/BasemapToggle.js",
    "https://js.arcgis.com/3.44/esri/layers/vectorTiles/core/workers/worker.js",
    "https://js.arcgis.com/3.44/dojo/resources/blank.gif",
    "https://js.arcgis.com/3.44/esri/config.js",
    "https://js.arcgis.com/3.44/esri/dijit/Scalebar.js",
    "https://js.arcgis.com/3.44/esri/layers/vectorTiles/core/workers/worker.js",
    "https://js.arcgis.com/3.44/esri/layers/support/nls/webglDeps_en-us.js",
    "https://js.arcgis.com/3.44/esri/layers/vectorTiles/core/workers/nls/worker-init_en-us.js"
]

# Calculate the correct JSON to compare with AI
def get_correct_answer(the_list):
    index = {}
    new_list = the_list.copy()
    new_list.sort()
    for word in new_list:
        if word in index:    
            index[word] += 1
        else:
            index[word] = 1
    return index

# Perform experiment
def experiment(version, mongo_collection, parameters):
    the_list = [
        "https://js.arcgis.com/3.44/esri/layers/RasterXLayer.js",
        "https://js.arcgis.com/3.44/esri/layers/ArcGISImageServiceLayer.js",
        "https://js.arcgis.com/3.44/esri/layers/vectorTiles/core/workers/worker.js"
    ]
    
    outcomes = []
    
# Run for list lengths of 4 words to 37 words
    for word in words:
        outcome = {}
        the_list.append(word)
        time.sleep(10)
        
# Retry until success        
        while True:
            res = chat(version, mongo_collection, [
            {
              "role": "user",
              "content": "Sort this list and group by URL. Provide the output in json form. Just give me the result, not a program. For example, if the list consists of 'a, b, a, c' then you will give me '{\"a\":2,\"b\":1,\"c\":1}'. Note that the keys are sorted alphabetically in the json.\n\nHere is the list:\n" + "\n".join(the_list)
            }
            ],parameters)
            res2 = get_correct_answer(the_list)
            try:
                answer = json.loads(res["choices"][0]["message"]["content"])
                break
            except:
                pass

        if json.dumps(answer,indent=2) != json.dumps(res2,indent=2):
            outcome["result"] = "FAIL"
        else:
            outcome["result"] = "PASS"
        outcome["wordlist"] = the_list.copy()
        outcome["chatgpt"] = answer
        outcome["python"] = get_correct_answer(the_list)
        outcomes.append(outcome)
    return outcomes   

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
    
# Run five trials    
    for trial in range(0,5):
        print(trial+1)
        answers.append(experiment(version, col, parameters))
        with open(filename,"wt") as f:
            print(json.dumps(answers,indent=2),file=f)