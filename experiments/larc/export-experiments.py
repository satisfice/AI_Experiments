import argparse
import csv
from pymongo import MongoClient


def export_experiments_to_csv(output_file='experiments.csv', mongo_uri='mongodb://localhost:27017/'):
    """
    Exports all COMPLETED experiment runs from the ARC database to a CSV file.
    One row per experiment with all metadata and metrics (excluding texts and item_details).

    Args:
        output_file: Output CSV file path
        mongo_uri: MongoDB connection URI
    """
    try:
        client = MongoClient(mongo_uri)
        arc_db = client["ARC"]

        # Get all collections in the ARC database
        collections = arc_db.list_collection_names()

        # Collect all completed experiments
        experiments = []

        print(f"Scanning {len(collections)} collections in ARC database...")

        for collection_name in collections:
            collection = arc_db[collection_name]

            # Find all completion records (only completed experiments)
            completion_records = collection.find({"type": "completion"})

            for completion_record in completion_records:
                test_run_id = completion_record.get('testRunId')

                # Extract metadata fields
                metadata = completion_record.get('metadata', {})

                # Extract metrics fields
                metrics = completion_record.get('metrics', {})

                # Extract survey_prompt_performance
                survey_perf = metrics.get('survey_prompt_performance', {})

                # Extract presence_prompt_performance
                presence_perf = metrics.get('presence_prompt_performance', {})

                # Convert mm:ss format to minutes (floating point)
                def mmss_to_minutes(mmss_str):
                    """Convert 'mm:ss' string to float minutes"""
                    if not mmss_str or mmss_str == '':
                        return ''
                    try:
                        parts = str(mmss_str).split(':')
                        if len(parts) == 2:
                            minutes = int(parts[0])
                            seconds = int(parts[1])
                            return round(minutes + seconds / 60.0, 4)
                        return ''
                    except:
                        return ''

                clocktime_minutes = mmss_to_minutes(metrics.get('clocktime_minutes', ''))
                api_time_minutes = mmss_to_minutes(metrics.get('api_time_mmss', ''))

                # Build the row dictionary
                row = {
                    'collection': collection_name,
                    'testRunId': test_run_id,

                    # Metadata fields
                    'timestamp': metadata.get('timestamp', ''),
                    'test_set': metadata.get('test_set', ''),
                    'test_id': metadata.get('test_id', ''),
                    'model': metadata.get('model', ''),
                    'temperature': metadata.get('temperature', ''),
                    'trials': metadata.get('trials', ''),

                    # Metrics fields
                    'text_size_estimated_tokens': metrics.get('text_size_estimated_tokens', ''),
                    'total_prompts': metrics.get('total_prompts', ''),
                    'clocktime_minutes': clocktime_minutes,
                    'api_time_minutes': api_time_minutes,
                    'total_unique_items': metrics.get('total_unique_items', ''),
                    'total_items_counted': metrics.get('total_items_counted', ''),
                    'repudiated_presence_pct': metrics.get('repudiated_presence_pct', ''),
                    'miss_rate': metrics.get('miss_rate', ''),
                    'ambivalence_pct': metrics.get('ambivalence_pct', ''),
                    'ambivalent_items_count': metrics.get('ambivalent_items_count', ''),
                    'ambivalent_items_total': metrics.get('ambivalent_items_total', ''),

                    # Survey prompt performance
                    'survey_mean_load_duration': survey_perf.get('mean_load_duration', ''),
                    'survey_mean_prompt_eval_duration': survey_perf.get('mean_prompt_eval_duration', ''),
                    'survey_mean_eval_duration': survey_perf.get('mean_eval_duration', ''),
                    'survey_mean_prompt_eval_count': survey_perf.get('mean_prompt_eval_count', ''),
                    'survey_mean_eval_count': survey_perf.get('mean_eval_count', ''),

                    # Presence prompt performance
                    'presence_mean_load_duration': presence_perf.get('mean_load_duration', ''),
                    'presence_mean_prompt_eval_duration': presence_perf.get('mean_prompt_eval_duration', ''),
                    'presence_mean_eval_duration': presence_perf.get('mean_eval_duration', ''),
                    'presence_mean_prompt_eval_count': presence_perf.get('mean_prompt_eval_count', ''),
                    'presence_mean_eval_count': presence_perf.get('mean_eval_count', ''),
                }

                experiments.append(row)

        if not experiments:
            print("No completed experiments found in the ARC database")
            return

        # Sort by timestamp (most recent first)
        experiments.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        # Define field order for CSV
        fieldnames = [
            'collection', 'testRunId', 'timestamp', 'test_set', 'test_id',
            'model', 'temperature', 'trials',
            'text_size_estimated_tokens', 'total_prompts', 'clocktime_minutes', 'api_time_minutes',
            'total_unique_items', 'total_items_counted', 'repudiated_presence_pct',
            'miss_rate', 'ambivalence_pct', 'ambivalent_items_count', 'ambivalent_items_total',
            'survey_mean_load_duration', 'survey_mean_prompt_eval_duration',
            'survey_mean_eval_duration', 'survey_mean_prompt_eval_count', 'survey_mean_eval_count',
            'presence_mean_load_duration', 'presence_mean_prompt_eval_duration',
            'presence_mean_eval_duration', 'presence_mean_prompt_eval_count', 'presence_mean_eval_count'
        ]

        # Write to CSV
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(experiments)

        print(f"\nExported {len(experiments)} completed experiments to {output_file}")
        print(f"\nFields exported:")
        for i, field in enumerate(fieldnames, 1):
            print(f"  {i:2d}. {field}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description='Export experiment data from ARC database to CSV')
    parser.add_argument('--output', '-o', default='experiments.csv',
                       help='Output CSV file path (default: experiments.csv)')
    parser.add_argument('--mongo-uri', default='mongodb://localhost:27017/',
                       help='MongoDB connection URI (default: mongodb://localhost:27017/)')

    args = parser.parse_args()

    export_experiments_to_csv(output_file=args.output, mongo_uri=args.mongo_uri)


if __name__ == "__main__":
    main()
