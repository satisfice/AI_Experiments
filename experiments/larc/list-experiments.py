import argparse
import os
import sys
import subprocess
from pymongo import MongoClient
from datetime import datetime


def list_experiments(mongo_uri='mongodb://localhost:27017/'):
    """
    Lists all experiment runs from the ARC database.

    Args:
        mongo_uri: MongoDB connection URI
    """
    try:
        client = MongoClient(mongo_uri)
        db = client["ARC"]

        # Get all collections in the ARC database
        collections = db.list_collection_names()

        # Collect all experiments across all collections
        experiments = []

        for collection_name in collections:
            collection = db[collection_name]

            # Find all start records in this collection
            start_records = collection.find({"type": "start"})

            for start_record in start_records:
                test_run_id = start_record.get('testRunId')
                test_id = start_record.get('metadata', {}).get('test_id', 'N/A')
                start_time = start_record.get('timestamp', 'N/A')

                # Look for corresponding completion record
                completion_record = collection.find_one({
                    "testRunId": test_run_id,
                    "type": "completion"
                })

                if completion_record:
                    end_time = completion_record.get('metadata', {}).get('timestamp', 'N/A')
                else:
                    end_time = "INCOMPLETE"

                # Count HTTP logging records for this testRunId across all collections
                http_logging_db = client["HTTP_logging"]
                http_record_count = 0
                for http_collection_name in http_logging_db.list_collection_names():
                    http_collection = http_logging_db[http_collection_name]
                    http_record_count += http_collection.count_documents({
                        "metadata.meta.header.testRunId": test_run_id
                    })

                experiments.append({
                    'collection': collection_name,
                    'test_id': test_id,
                    'test_run_id': test_run_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'http_records': http_record_count
                })

        # Sort experiments by start_time (newest first)
        def parse_timestamp(exp):
            try:
                # Try to parse the timestamp
                ts = exp['start_time']
                if ts == 'N/A':
                    return datetime.min
                return datetime.strptime(ts, '%Y-%m-%d %H:%M:%S %Z')
            except:
                return datetime.min

        experiments.sort(key=parse_timestamp, reverse=True)

        # Print header
        print(f"#\tCollection\tTest ID\tTest Run ID\tStart Time\tEnd Time\tHTTP Records")
        print("-" * 160)

        # Print each experiment
        for i, exp in enumerate(experiments, 1):
            print(f"{i}\t{exp['collection']}\t{exp['test_id']}\t{exp['test_run_id']}\t{exp['start_time']}\t{exp['end_time']}\t{exp['http_records']}")

        print()
        print(f"Total experiments: {len(experiments)}")
        incomplete = sum(1 for exp in experiments if exp['end_time'] == 'INCOMPLETE')
        print(f"Incomplete experiments: {incomplete}")

    except Exception as e:
        print(f"Error: {e}")


def remove_experiment(row_number, mongo_uri='mongodb://localhost:27017/'):
    """
    Removes an experiment and its associated records from ARC and HTTP_logging databases.

    Args:
        row_number: The row number from the experiment list
        mongo_uri: MongoDB connection URI
    """
    try:
        client = MongoClient(mongo_uri)
        db = client["ARC"]

        # Get all collections in the ARC database
        collections = db.list_collection_names()

        # Collect all experiments across all collections (same as list_experiments)
        experiments = []

        for collection_name in collections:
            collection = db[collection_name]

            # Find all start records in this collection
            start_records = collection.find({"type": "start"})

            for start_record in start_records:
                test_run_id = start_record.get('testRunId')
                test_id = start_record.get('metadata', {}).get('test_id', 'N/A')
                start_time = start_record.get('timestamp', 'N/A')

                # Look for corresponding completion record
                completion_record = collection.find_one({
                    "testRunId": test_run_id,
                    "type": "completion"
                })

                if completion_record:
                    end_time = completion_record.get('metadata', {}).get('timestamp', 'N/A')
                else:
                    end_time = "INCOMPLETE"

                # Count HTTP logging records for this testRunId across all collections
                http_logging_db = client["HTTP_logging"]
                http_record_count = 0
                for http_collection_name in http_logging_db.list_collection_names():
                    http_collection = http_logging_db[http_collection_name]
                    http_record_count += http_collection.count_documents({
                        "metadata.meta.header.testRunId": test_run_id
                    })

                experiments.append({
                    'collection': collection_name,
                    'test_id': test_id,
                    'test_run_id': test_run_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'http_records': http_record_count
                })

        # Sort experiments by start_time (newest first)
        def parse_timestamp(exp):
            try:
                ts = exp['start_time']
                if ts == 'N/A':
                    return datetime.min
                return datetime.strptime(ts, '%Y-%m-%d %H:%M:%S %Z')
            except:
                return datetime.min

        experiments.sort(key=parse_timestamp, reverse=True)

        # Validate row number
        if row_number < 1 or row_number > len(experiments):
            print(f"Error: Row number {row_number} is out of range (1-{len(experiments)})")
            return

        # Get the experiment to remove
        exp = experiments[row_number - 1]
        test_run_id = exp['test_run_id']
        collection_name = exp['collection']

        print(f"Removing experiment:")
        print(f"  Row: {row_number}")
        print(f"  Collection: {collection_name}")
        print(f"  Test ID: {exp['test_id']}")
        print(f"  Test Run ID: {test_run_id}")
        print(f"  HTTP Records: {exp['http_records']}")
        print()

        # Confirm deletion
        confirm = input("Are you sure you want to delete this experiment? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Deletion cancelled.")
            return

        # Delete from ARC database
        arc_collection = db[collection_name]
        arc_result = arc_collection.delete_many({"testRunId": test_run_id})
        print(f"Deleted {arc_result.deleted_count} records from ARC.{collection_name}")

        # Delete from HTTP_logging database (all collections)
        http_logging_db = client["HTTP_logging"]
        total_http_deleted = 0
        for http_collection_name in http_logging_db.list_collection_names():
            http_collection = http_logging_db[http_collection_name]
            http_result = http_collection.delete_many({
                "metadata.meta.header.testRunId": test_run_id
            })
            if http_result.deleted_count > 0:
                print(f"Deleted {http_result.deleted_count} records from HTTP_logging.{http_collection_name}")
                total_http_deleted += http_result.deleted_count

        if total_http_deleted == 0:
            print("No records found in HTTP_logging database")

        print()
        print("Experiment successfully removed.")

    except Exception as e:
        print(f"Error: {e}")


def generate_report(row_number, mongo_uri='mongodb://localhost:27017/'):
    """
    Generates an HTML report for the experiment at the specified row number.

    Args:
        row_number: The row number from the experiment list
        mongo_uri: MongoDB connection URI
    """
    try:
        client = MongoClient(mongo_uri)
        db = client["ARC"]

        # Get all collections in the ARC database
        collections = db.list_collection_names()

        # Collect all experiments across all collections (same as list_experiments)
        experiments = []

        for collection_name in collections:
            collection = db[collection_name]

            # Find all start records in this collection
            start_records = collection.find({"type": "start"})

            for start_record in start_records:
                test_run_id = start_record.get('testRunId')
                test_id = start_record.get('metadata', {}).get('test_id', 'N/A')
                start_time = start_record.get('timestamp', 'N/A')

                # Look for corresponding completion record
                completion_record = collection.find_one({
                    "testRunId": test_run_id,
                    "type": "completion"
                })

                if completion_record:
                    end_time = completion_record.get('metadata', {}).get('timestamp', 'N/A')
                else:
                    end_time = "INCOMPLETE"

                # Count HTTP logging records for this testRunId across all collections
                http_logging_db = client["HTTP_logging"]
                http_record_count = 0
                for http_collection_name in http_logging_db.list_collection_names():
                    http_collection = http_logging_db[http_collection_name]
                    http_record_count += http_collection.count_documents({
                        "metadata.meta.header.testRunId": test_run_id
                    })

                experiments.append({
                    'collection': collection_name,
                    'test_id': test_id,
                    'test_run_id': test_run_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'http_records': http_record_count
                })

        # Sort experiments by start_time (newest first)
        def parse_timestamp(exp):
            try:
                ts = exp['start_time']
                if ts == 'N/A':
                    return datetime.min
                return datetime.strptime(ts, '%Y-%m-%d %H:%M:%S %Z')
            except:
                return datetime.min

        experiments.sort(key=parse_timestamp, reverse=True)

        # Validate row number
        if row_number < 1 or row_number > len(experiments):
            print(f"Error: Row number {row_number} is out of range (1-{len(experiments)})")
            return

        # Get the experiment to generate report for
        exp = experiments[row_number - 1]
        test_run_id = exp['test_run_id']
        test_id = exp['test_id']

        # Check if experiment is complete
        if exp['end_time'] == 'INCOMPLETE':
            print(f"Warning: Experiment at row {row_number} is incomplete.")
            print("Report may not contain all expected data.")
            print()

        print(f"Generating report for:")
        print(f"  Row: {row_number}")
        print(f"  Collection: {exp['collection']}")
        print(f"  Test ID: {test_id}")
        print(f"  Test Run ID: {test_run_id}")
        print()

        # Create experiments/run_reports directory if it doesn't exist
        report_dir = os.path.join('experiments', 'run_reports')
        os.makedirs(report_dir, exist_ok=True)

        # Construct output file path
        output_html = os.path.join(report_dir, f"{test_id}.htm")

        # Call larc-report.py
        try:
            result = subprocess.run([
                sys.executable,
                'larc-report.py',
                '--test-run-id', test_run_id,
                '--mongo-uri', mongo_uri,
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

    except Exception as e:
        print(f"Error: {e}")


def generate_all_reports(mongo_uri='mongodb://localhost:27017/'):
    """
    Generates HTML reports for all completed experiments in the database.

    Args:
        mongo_uri: MongoDB connection URI
    """
    try:
        client = MongoClient(mongo_uri)
        db = client["ARC"]

        # Get all collections in the ARC database
        collections = db.list_collection_names()

        # Collect all experiments across all collections
        experiments = []

        for collection_name in collections:
            collection = db[collection_name]

            # Find all start records in this collection
            start_records = collection.find({"type": "start"})

            for start_record in start_records:
                test_run_id = start_record.get('testRunId')
                test_id = start_record.get('metadata', {}).get('test_id', 'N/A')
                start_time = start_record.get('timestamp', 'N/A')

                # Look for corresponding completion record
                completion_record = collection.find_one({
                    "testRunId": test_run_id,
                    "type": "completion"
                })

                if completion_record:
                    end_time = completion_record.get('metadata', {}).get('timestamp', 'N/A')
                else:
                    end_time = "INCOMPLETE"

                # Count HTTP logging records for this testRunId across all collections
                http_logging_db = client["HTTP_logging"]
                http_record_count = 0
                for http_collection_name in http_logging_db.list_collection_names():
                    http_collection = http_logging_db[http_collection_name]
                    http_record_count += http_collection.count_documents({
                        "metadata.meta.header.testRunId": test_run_id
                    })

                experiments.append({
                    'collection': collection_name,
                    'test_id': test_id,
                    'test_run_id': test_run_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'http_records': http_record_count
                })

        print(f"Found {len(experiments)} experiments")

        # Filter to only completed experiments
        completed = [exp for exp in experiments if exp['end_time'] != 'INCOMPLETE']
        print(f"Completed experiments: {len(completed)}")

        if len(completed) == 0:
            print("No completed experiments to generate reports for.")
            return

        # Create experiments/run_reports directory if it doesn't exist
        report_dir = os.path.join('experiments', 'run_reports')
        os.makedirs(report_dir, exist_ok=True)

        # Generate report for each completed experiment
        success_count = 0
        error_count = 0

        for i, exp in enumerate(completed, 1):
            test_run_id = exp['test_run_id']
            test_id = exp['test_id']

            print(f"\n[{i}/{len(completed)}] Generating report for {test_id}...")
            print(f"  Test Run ID: {test_run_id}")

            # Construct output file path
            output_html = os.path.join(report_dir, f"{test_id}.htm")

            # Call larc-report.py
            try:
                result = subprocess.run([
                    sys.executable,
                    'larc-report.py',
                    '--test-run-id', test_run_id,
                    '--mongo-uri', mongo_uri,
                    '--output-file', output_html
                ], check=True, capture_output=True, text=True)

                print(f"  ✓ Report generated: {output_html}")
                success_count += 1

            except subprocess.CalledProcessError as e:
                print(f"  ✗ Error generating report: {e}")
                if e.stderr:
                    print(f"  {e.stderr}")
                error_count += 1
            except Exception as e:
                print(f"  ✗ Error: {e}")
                error_count += 1

        print()
        print("="*60)
        print(f"Report generation complete:")
        print(f"  Success: {success_count}")
        print(f"  Errors: {error_count}")
        print(f"  Reports saved to: {report_dir}")

    except Exception as e:
        print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description='List all experiment runs from ARC database')
    parser.add_argument('--mongo-uri', type=str, default='mongodb://localhost:27017/',
                       help='MongoDB connection URI (default: mongodb://localhost:27017/)')
    parser.add_argument('--rm', type=int, metavar='ROW_NUMBER',
                       help='Remove experiment at the specified row number')
    parser.add_argument('--report', type=int, metavar='ROW_NUMBER',
                       help='Generate HTML report for experiment at the specified row number')
    parser.add_argument('--reportall', action='store_true',
                       help='Generate HTML reports for all completed experiments')

    args = parser.parse_args()

    if args.rm:
        remove_experiment(args.rm, args.mongo_uri)
    elif args.report:
        generate_report(args.report, args.mongo_uri)
    elif args.reportall:
        generate_all_reports(args.mongo_uri)
    else:
        list_experiments(args.mongo_uri)


if __name__ == '__main__':
    main()
