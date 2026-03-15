from collections import defaultdict
from datetime import datetime, timezone
import json
import sys
import time
import argparse
import pickle
import uuid
import re
import os
import subprocess
from pymongo import MongoClient
from pydantic import BaseModel

# Set UTF-8 encoding for stdout to handle Unicode characters
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.append("c:/code/logged_requests")
import logged_requests

class Metadata:
    def __init__(self,testRoutine, testRunId, sut, description, testSet, testId="NA", testStep="NA"):
        self.testRoutine = testRoutine
        self.testRunId = testRunId
        self.sut = sut
        self.description = description
        self.testSet = testSet
        self.testId = testId
        self.testStep = testStep

    def dump(self):
        return {"testRoutine":self.testRoutine, "testRunId":self.testRunId, "SUT":self.sut, "description":self.description, "testSet":self.testSet, "testId":self.testId, "testStep":self.testStep}

class SurveyResponse(BaseModel):
    results: list[str]

class PresenceResponse(BaseModel):
    exists: list[str]

def retry_logged_request(http, header, method, url, json_data=None, max_retries=5, **kwargs):
    """
    Wrapper for logged_request that retries up to max_retries times.
    Success criteria: response is JSON with non-empty 'response' key.
    """

    for attempt in range(1, max_retries + 1):
        try:
            if json_data:             
                response = http.logged_request(header, method, url, json=json_data, **kwargs)
            else:
                response = http.logged_request(header, method, url, **kwargs)

            # Check if response is valid JSON with non-empty 'response' key
            return response.json()

        except Exception as e:
            print(f"Attempt {attempt}/{max_retries}: Request failed with error: {e}")

        if attempt < max_retries:
            print(f"Retrying in 30 seconds...")
            time.sleep(30)

    print(f"Failed after {max_retries} attempts. Exiting.")
    sys.exit(1)

def get_total_duration(testRunId, mongo_uri='mongodb://localhost:27017/'):
    """
    Queries the HTTP_logging.ollama collection for all records with the given testRunId,
    sums up the durations in metadata.meta.duration, and returns formatted duration string.

    Args:
        testRunId: The test run ID to search for
        mongo_uri: MongoDB connection URI

    Returns:
        String in format "mm:ss" representing total duration
    """
    try:
        client = MongoClient(mongo_uri)
        db = client["HTTP_logging"]
        collection = db["openai"]

        # Query for all records with this testRunId
        records = collection.find({"metadata.meta.header.testRunId": testRunId})

        # Sum up all durations
        total_seconds = 0
        for record in records:
            try:
                duration = record.get('metadata', {}).get('duration', 0)
                total_seconds += float(re.search("(.*?) seconds",duration).group(1))
            except Exception as e:
                print(f"Warning: Could not parse duration from record: {e}")
                continue

        # Convert to minutes and seconds
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)

        return f"{minutes:02d}:{seconds:02d}"

    except Exception as e:
        print(f"Error querying MongoDB: {e}")
        return "00:00"


# Parse command-line arguments
parser = argparse.ArgumentParser(description='Test LLM Aggregated Retrieval Consistency')
parser.add_argument('--config', type=str, help='JSON config file containing all settings (overrides other arguments)')
parser.add_argument('--source-file', type=str, help='File containing source text to analyze (required unless --continue is used)')
parser.add_argument('--survey-prompt-file', type=str, help='File containing survey prompt template (required unless --continue is used)')
parser.add_argument('--presence-prompt-file', type=str, help='File containing presence check prompt template (required unless --continue is used)')
parser.add_argument('--temperature', type=float, default=0.7, help='Temperature for LLM generation (default: 0.7)')
parser.add_argument('--model', type=str, default='llama3.1:8b', help='Model to use (default: llama3.1:8b)')
parser.add_argument('--testid', type=str, default='N/A', help='Test ID (default: N/A)')
parser.add_argument('--description', type=str, default='N/A', help='Test description (default: N/A)')
parser.add_argument('--testset', type=str, default='N/A', help='Test set name (default: N/A)')
parser.add_argument('--trials', type=int, default=10, help='Number of trials to run (default: 10)')
parser.add_argument('--mongo-uri', type=str, default='mongodb://localhost:27017/', help='MongoDB connection URI (default: mongodb://localhost:27017/)')
parser.add_argument('--continue', type=str, dest='continue_file', help='Continue from saved state file (pkl format)')
parser.add_argument('--output-file', type=str, help='File to save final results JSON (in addition to console and MongoDB)')
parser.add_argument('--report', action='store_true', help='Generate HTML report automatically after completion')

state_file = 'experiment_state.pkl'

# Parse command line args, keeping track of which were explicitly provided
args, unknown = parser.parse_known_args()
cli_provided = set()

# Parse again with full error checking, but capture which args were in sys.argv
import sys
for arg in sys.argv[1:]:
    if arg.startswith('--'):
        arg_name = arg.split('=')[0].lstrip('--').replace('-', '_')
        cli_provided.add(arg_name)

args = parser.parse_args()

# Load config file if specified
if args.config:
    try:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # Apply config values only if not explicitly provided on command line
        config_keys = ['source_file', 'survey_prompt_file', 'presence_prompt_file',
                       'temperature', 'model', 'testid', 'description', 'testset',
                       'trials', 'mongo_uri', 'output_file']

        for key in config_keys:
            if key not in cli_provided:
                setattr(args, key, config.get(key, getattr(args, key)))

        print(f"Loaded configuration from {args.config}")
    except FileNotFoundError:
        print(f"Error: Config file {args.config} not found")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in config file {args.config}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading config file {args.config}: {e}")
        sys.exit(1)

# Handle --continue mode vs normal mode
if args.continue_file:
    # When --continue is used, prohibit all other arguments that were explicitly provided
    prohibited_args = []
    for arg_name in cli_provided:
        if arg_name != 'continue' and arg_name != 'continue_file':
            # Convert back to command-line format
            cli_arg = '--' + arg_name.replace('_', '-')
            prohibited_args.append(cli_arg)

    if prohibited_args:
        print(f"Error: When using --continue, these arguments are prohibited: {', '.join(prohibited_args)}")
        print("All settings are restored from the saved state file.")
        sys.exit(1)

    # Load saved state
    try:
        with open(args.continue_file, 'rb') as f:
            state = pickle.load(f)
        print(f"Restoring state from {args.continue_file}")
        print(f"Continuing from trial {state['trial'] + 1} of {state['trials']}")

        # Restore variables from state
        model = state['model']
        temperature = state['temperature']
        trials = state['trials']
        start_time = state['start_time']
        source_text = state['source_text']
        survey_prompt_template = state['survey_prompt_template']
        presence_prompt_template = state['presence_prompt_template']
        counted_items = state['counted_items']
        total = state['total']
        existence = defaultdict(list, state['existence'])
        meta_dict = state['meta_dict']
        survey_perf = state.get('survey_perf', {'load_duration': [], 'prompt_eval_duration': [], 'eval_duration': [], 'prompt_eval_count': [], 'eval_count': []})
        presence_perf = state.get('presence_perf', {'load_duration': [], 'prompt_eval_duration': [], 'eval_duration': [], 'prompt_eval_count': [], 'eval_count': []})

        continuing = True
        start_trial = state['trial'] + 1

    except FileNotFoundError:
        print(f"State file {args.continue_file} not found")
        sys.exit(1)
    except Exception as e:
        print(f"Failed to restore state: {e}")
        sys.exit(1)
else:
    # Normal mode: validate required arguments
    missing_args = []
    if not args.source_file:
        missing_args.append('--source-file')
    if not args.survey_prompt_file:
        missing_args.append('--survey-prompt-file')
    if not args.presence_prompt_file:
        missing_args.append('--presence-prompt-file')

    if missing_args:
        print(f"Error: The following arguments are required when not using --continue: {', '.join(missing_args)}")
        sys.exit(1)

    # Initialize for new run
    continuing = False
    start_trial = 1
    model = args.model
    temperature = args.temperature
    trials = args.trials

    # Create unique test run ID
    testRunGUID = uuid.uuid4().hex
    print(f"Test Run ID: {testRunGUID}")

    # Log time at start of run
    start_time = time.time()

    # Read source text and prompts from files
    with open(args.source_file, 'r', encoding='utf-8') as f:
        source_text = f.read().strip()

    with open(args.survey_prompt_file, 'r', encoding='utf-8') as f:
        survey_prompt_template = f.read().strip()

    with open(args.presence_prompt_file, 'r', encoding='utf-8') as f:
        presence_prompt_template = f.read().strip()

    # Initialize data structures
    total = []
    existence = defaultdict(list)
    counted_items = {}

    # Create metadata
    meta = Metadata(testRoutine="openai", testRunId=testRunGUID, sut="/v1/chat/completions", description=args.description, testSet=args.testset, testId=args.testid, testStep="first_prompt")

    # Log start record to MongoDB
    start_record = {
        "testRunId": testRunGUID,
        "type": "start",
        "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z'),
        "metadata": {
            "test_set": args.testset,
            "test_id": args.testid,
            "description": args.description
        },
        "config": {
            "source_file": args.source_file,
            "survey_prompt_file": args.survey_prompt_file,
            "presence_prompt_file": args.presence_prompt_file,
            "temperature": temperature,
            "model": model,
            "testid": args.testid,
            "description": args.description,
            "testset": args.testset,
            "trials": trials,
            "mongo_uri": args.mongo_uri,
            "output_file": args.output_file
        }
    }

    try:
        client = MongoClient(args.mongo_uri)
        db = client["ARC"]
        collection_name = args.testset if args.testset != "N/A" else "default_testset"
        collection = db[collection_name]
        collection.insert_one(start_record)
        print(f"Start record logged to MongoDB collection: {collection_name}")
    except Exception as e:
        print(f"Failed to store start record in MongoDB: {e}")

API_KEY = os.getenv("OPENAI_API_KEY")  # or paste your key directly (not recommended)
url = "https://api.openai.com/v1/responses"

http = logged_requests.loggedRequests(logResponse=True, logName="openai", caching=False, vcrmode=False)

# Run initial survey (only for new runs, not when continuing)
if not continuing:
    for trial in range(1, trials + 1):
        meta.testStep = "survey_trial_" + str(trial) + "_of_" + str(trials)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}"
        }
        data = {
            "model": model,
            "temperature": temperature,
            "text": { "format": { "type": "json_object" } },
            "input": survey_prompt_template + "\n" + source_text
        }
        resp = retry_logged_request(http, {"header": meta.dump()}, "POST", url, headers=headers, json_data=data)
        print(resp.get("output","")[0].get("content","")[0].get("text", ""))
        print("---------------------------")
        thisround = [i.lower() for i in json.loads(resp.get("output","")[0].get("content","")[0].get("text", "")).get("results","")]
        total.extend(set(thisround))
    # Count items
    for item in total:
        counted_items[item.lower()] = counted_items.get(item.lower(), 0) + 1

    print(json.dumps(counted_items, indent=2, sort_keys=True))
else:
    # When continuing, create metadata from saved state
    meta = Metadata(meta_dict['testRoutine'], meta_dict['testRunId'], meta_dict['SUT'], meta_dict['description'], meta_dict['testSet'], meta_dict['testId'], meta_dict['testStep'])

# Main processing loop - start from appropriate trial
for trial in range(start_trial, trials + 1):
    for item in counted_items:
        meta.testStep = f"check_phrases_existence_{item}_trial_{trial}_of_{trials}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}"
        }
        data = {
            "model": model,
            "temperature": temperature,
            "text": { "format": { "type": "json_object" } },
            "input":  presence_prompt_template.replace("{item}", item) + "\n" + source_text
        }
        resp = retry_logged_request(http, {"header": meta.dump()}, "POST", url, headers=headers, json_data=data)

        print(f"Progress: {( ( (trial - 1) * len(counted_items) + list(counted_items).index(item) + 1) / (trials * len(counted_items)) ) * 100:.2f}%", end='\r')

        existence[item].append(json.loads(resp.get("output","")[0].get("content","")[0].get("text", "")).get("exists",""))

        # Save state at end of each trial
    state = {
        'trial': trial,
        'counted_items': counted_items,
        'existence': dict(existence),
        'total': total,
        'start_time': start_time,
        'source_text': source_text,
        'survey_prompt_template': survey_prompt_template,
        'presence_prompt_template': presence_prompt_template,
        'model': model,
        'temperature': temperature,
        'trials': trials,
        'meta_dict': meta.dump()
    }
    with open(state_file, 'wb') as f:
        pickle.dump(state, f)

# Calculate totals for final results
total_counted = sum(counted_items.values())
total_rp = sum(existence[item].count(False) for item in counted_items)
# Count items that were found in every trial and never repudiated
imperfect = sum(1 for item in counted_items
                if existence[item].count(False) > 0 or counted_items[item] < trials)

print()

# Final Results
print()
print("Final Results")
print(f"Test Run ID\t{meta.testRunId}")
print(f"Time Completed\t{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
print(f"Test\t{meta.testId}")
print(f"Model\t{model}")
print(f"Temperature\t{temperature}")
print(f"Size of text (estimated tokens)\t{round(len(source_text)/4/10)*10}")
print(f"Total prompts\t{len(counted_items)*trials+trials}")
total_seconds = time.time() - start_time
minutes = int(total_seconds // 60)
seconds = int(total_seconds % 60)
clocktime_mmss = f"{minutes:02d}:{seconds:02d}"
print(f"Run time (start to end) (mm:ss)\t{clocktime_mmss}")
print(f"API time (mm:ss)\t{get_total_duration(testRunGUID, args.mongo_uri)}")
print(f"Total unique items\t{len(counted_items)}")
print(f"Total items counted\t{total_counted}")
print(f"Trials\t{trials}")
print()

print("Repudiated Presence %\t{:.2f}".format((total_rp/ (len(counted_items)*trials))*100))
print("Miss Rate %\t{:.2f}".format(((len(counted_items)*trials - total_counted)/ (len(counted_items)*trials))*100))
print("Ambivalence %\t{:.2f}\t({}/{})".format((imperfect/len(counted_items))*100, imperfect, len(counted_items)))
print()

# Prepare detailed item results
item_results = []
for item in sorted(counted_items):
    item_results.append({
        "phrase": item,
        "count": counted_items[item],
        "repudiation_count": existence[item].count(False)
    })

# Create completion results JSON
completion_results = {
    "testRunId": testRunGUID,
    "type": "completion",
    "texts": {
        "source": source_text,
        "survey_prompt": survey_prompt_template,
        "presence_prompt": presence_prompt_template
    },
    "metadata": {
        "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z'),
        "test_run_id": testRunGUID,
        "test_set": meta.testSet,
        "test_id": meta.testId,
        "model": model,
        "temperature": temperature,
        "trials": trials
    },
    "metrics": {
        "text_size_estimated_tokens": round(len(source_text)/4/10)*10,
        "total_prompts": len(counted_items)*trials+trials,
        "clocktime_minutes": clocktime_mmss,
        "api_time_mmss": get_total_duration(testRunGUID, args.mongo_uri),
        "total_unique_items": len(counted_items),
        "total_items_counted": total_counted,
        "repudiated_presence_pct": (total_rp/ (len(counted_items)*trials))*100 if counted_items else 0,
        "miss_rate": ((len(counted_items)*trials - total_counted)/ (len(counted_items)*trials))*100 if counted_items else 0,
        "ambivalence_pct": (imperfect/len(counted_items))*100 if counted_items else 0,
        "ambivalent_items_count": imperfect,
        "ambivalent_items_total": len(counted_items)
    },
    "item_details": item_results
}

# Store completion results in MongoDB
try:
    client = MongoClient(args.mongo_uri)
    db = client["ARC"]
    collection_name = args.testset if args.testset != "N/A" else "default_testset"
    collection = db[collection_name]
    result = collection.insert_one(completion_results)
except Exception as e:
    print(f"Failed to store completion record in MongoDB: {e}")

# Save results to file if specified
if args.output_file:
    try:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            json.dump(completion_results, f, indent=2, ensure_ascii=False)
        print(f"Results saved to: {args.output_file}")
    except Exception as e:
        print(f"Failed to save results to file {args.output_file}: {e}")

print()
print(f"Phrase\tIdentified\tRepudiated Presence")
for item_result in item_results:
    print(f"{item_result['phrase']:<40}\t{item_result['count']:<5}\t{item_result['repudiation_count']:<5}")

# Generate HTML report if requested
if args.report:
    print()
    print("Generating HTML report...")

    # Create experiments/run_reports directory if it doesn't exist
    report_dir = os.path.join('experiments', 'run_reports')
    os.makedirs(report_dir, exist_ok=True)

    # Construct output file path
    output_html = os.path.join(report_dir, f"{meta.testId}.htm")

    # Call larc-report.py
    try:
        result = subprocess.run([
            sys.executable,
            'larc-report.py',
            '--test-run-id', testRunGUID,
            '--mongo-uri', args.mongo_uri,
            '--output-file', output_html
        ], check=True, capture_output=True, text=True)

        print(f"HTML report generated: {output_html}")
        if result.stdout:
            print(result.stdout)

    except subprocess.CalledProcessError as e:
        print(f"Error generating report: {e}")
        if e.stderr:
            print(e.stderr)
    except Exception as e:
        print(f"Error running larc-report.py: {e}")

