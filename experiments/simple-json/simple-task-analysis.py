import json

files = [
	["simple-task-experiment1d-T00M35-result1.json", .0,35],
	["simple-task-experiment1d-T03M35-result1.json", .3,35],
	["simple-task-experiment1d-T08M35-result1.json", .8,35],
	["simple-task-experiment1d-T00M40-result1.json", .0,40],
	["simple-task-experiment1d-T03M40-result1.json", .3,40],
	["simple-task-experiment1d-T08M40-result1.json", .8,40]
]

experiments = []

for file in files:
    with open("data/" + file[0]) as f:
        results = json.load(f)
        experiment_data = []
        for trial in range (1,len(results)):
            trial_data = []
            fail_type = []
            for r in results[trial]:
                list_miscount = False
                item_miscount = False
                if len(r["chatgpt"]) != len(r["python"]):
                    list_miscount = True
                for item in r["python"]:
                    if item in r["chatgpt"]:
                        if r["python"][item] != r["chatgpt"][item]:
                            item_miscount = True
                    else:
                        item_miscount = True
                if r["result"] == "FAIL":
                    if list_miscount == False and item_miscount == False:
                        fail_type.append("BAD SORT")
                    else:
                        fail_type.append("MISCOUNT")
                else:
                    fail_type.append("PASS")
                trial_data.append(r["result"])
            experiment_data.append([trial_data,fail_type])
        experiments.append(experiment_data)

labels = [
        str(files[0][1])+"-"+str(files[0][2]),
        str(files[1][1])+"-"+str(files[1][2]),
        str(files[2][1])+"-"+str(files[2][2]),
        str(files[3][1])+"-"+str(files[3][2]),
        str(files[4][1])+"-"+str(files[4][2]),
        str(files[5][1])+"-"+str(files[5][2]),
    ]
for i in range(0,len(experiments)):
    print("\t"+labels[i]+"\t\t\t\t",end="\t")
print()
for i in range(0,len(experiments)):
    print("\ttrial 1\ttrial 2\ttrial 3\ttrial 4\ttrial 5",end="\t")
print()
for test in range(0,len(experiments[0][0][0])):
    for i in range(0,len(experiments)):
            print(test+4,end="\t")
            for trial in range(0,5):
                print(experiments[i][trial][0][test],end="\t")
    print()
print()
for test in range(0,len(experiments[0][0][0])):
    for i in range(0,len(experiments)):
            print(test+4,end="\t")
            for trial in range(0,5):
                print(experiments[i][trial][1][test],end="\t")
    print()
    
    