import sys
import argparse
import csv
from pymongo import MongoClient


def extract_stats(testRunId, mongo_uri='mongodb://localhost:27017/', output_file='stats.tsv'):
    """
    Extracts performance statistics from MongoDB for a given testRunId.

    Args:
        testRunId: The test run ID to search for
        mongo_uri: MongoDB connection URI
        output_file: Output TSV file path
    """
    try:
        client = MongoClient(mongo_uri)
        db = client["HTTP_logging"]
        collection = db["ollama"]

        # Find all records with matching testRunId
        records = collection.find({"metadata.meta.header.testRunId": testRunId})

        # Collect data
        data = []
        record_count = 0

        for record in records:
            record_count += 1

            # Extract fields with safe navigation
            record_id = str(record.get('_id', ''))
            test_step = record.get('metadata', {}).get('meta', {}).get('header', {}).get('testStep', '')
            duration = record.get('metadata', {}).get('duration', '')
            # Strip " seconds" from duration field
            if isinstance(duration, str) and duration.endswith(' seconds'):
                duration = duration[:-8]  # Remove last 8 characters (" seconds")
            load_duration = record.get('content', {}).get('load_duration', '')
            prompt_eval_duration = record.get('content', {}).get('prompt_eval_duration', '')
            eval_duration = record.get('content', {}).get('eval_duration', '')
            prompt_eval_count = record.get('content', {}).get('prompt_eval_count', '')
            eval_count = record.get('content', {}).get('eval_count', '')

            data.append({
                'record_id': record_id,
                'testStep': test_step,
                'duration': duration,
                'load_duration': load_duration,
                'prompt_eval_duration': prompt_eval_duration,
                'eval_duration': eval_duration,
                'prompt_eval_count': prompt_eval_count,
                'eval_count': eval_count
            })

        if record_count == 0:
            print(f"No records found for testRunId: {testRunId}")
            sys.exit(1)

        print(f"Found {record_count} records for testRunId: {testRunId}")

        # Write to TSV file
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['record_id', 'testStep', 'duration', 'load_duration', 'prompt_eval_duration',
                         'eval_duration', 'prompt_eval_count', 'eval_count']
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter='\t')

            writer.writeheader()
            writer.writerows(data)

        print(f"Statistics written to: {output_file}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Extract performance statistics from MongoDB')
    parser.add_argument('testrunid', type=str, help='Test run ID to search for')
    parser.add_argument('--mongo-uri', type=str, default='mongodb://localhost:27017/',
                       help='MongoDB connection URI (default: mongodb://localhost:27017/)')
    parser.add_argument('--output', type=str, default='stats.tsv',
                       help='Output TSV file path (default: stats.tsv)')

    args = parser.parse_args()

    extract_stats(args.testrunid, args.mongo_uri, args.output)


if __name__ == '__main__':
    main()
