"""
Cascade compliance reports and make the final complinace report.
The final report is stored as a json file.
"""

import os
import json
import boto3
from urllib.parse import urlparse
import csv
import io
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def sorter(final_report):
    """"
    Sort the final report according to scene_index first and then frame_start_index.
    """
    # Convert dictionary of lists to list of dictionaries for sorting
    records = []
    for i in range(len(final_report["scene_index"])):
        record = {key: final_report[key][i] for key in final_report.keys()}
        records.append(record)

    # Sort by scene_index and then by frame_start_index
    sorted_records = sorted(records, key=lambda x: (x["scene_index"], x["frame_start_index"]))

    # Convert back to dictionary of lists
    final_report = {key: [record[key] for record in sorted_records] for key in final_report.keys()}

    return final_report

def s3list(bucket_name, video_id, folder):
    """
    List all JSON files in a specific folder under bucket_name/video_id.

    Args:
        bucket_name: Name of the S3 bucket
        video_id: ID of the video
        folder: Folder name under video_id

    Returns:
        List of JSON file names
    """
    s3 = boto3.client('s3')

    # Create the prefix for the S3 path
    prefix = os.path.join(video_id, folder)

    # Parse bucket name
    parsed_url = urlparse(bucket_name)
    if parsed_url.scheme == "s3":
        bucket = parsed_url.netloc
    else:
        bucket = bucket_name

    # Initialize the paginator
    paginator = s3.get_paginator('list_objects_v2')
    # Create a PageIterator from the paginator
    page_iterator = paginator.paginate(
        Bucket=bucket_name,
        Prefix=prefix
    )

    json_files = []

    # Check if there are any objects
    for page in page_iterator:
        if "Contents" in page:
            # Loop through all files and filter for JSON files
            for obj in page["Contents"]:
                key = obj["Key"]
                if key.endswith(".json"):
                    json_files.append(key)

    return json_files

def errorchecker(bucket_name, video_id):
    """
    Check missing generated complinace reports by comparing to the extracted metadata files
    """
    s3_client = boto3.client("s3", region_name="us-east-1")
    # parsed_url = urlparse(bucket_name)
    # if parsed_url.scheme != "s3":
    #     raise ValueError(f"Invalid S3 URL scheme: {parsed_url.scheme}. Expected 's3'")
    # bucket = parsed_url.netloc

    generator_bucket = os.environ['GENERATOR_BUCKET_NAME']
    extract = [f for f in s3list(generator_bucket, 'metadata', video_id) if f.endswith('_metadata.json')]
    generate = s3list(generator_bucket, 'shot_report', video_id)
    generate = [int(path.split('/')[-1].split('_')[1]) for path in generate] # extract only the shot index name
    missing = []
    for name in extract:
        extract_index = int(name.split('/')[-1].split('_')[0])
        if extract_index not in generate:
            missing.append(extract_index)
    missing = sorted(missing)
    error_report = {'missing_shot_report_index': missing, 'total_missing_count': len(missing)}

    # Save it to a json file
    output_bucket = os.environ['BUCKET_NAME']
    final_error_report_json_key = os.path.join("final_report", video_id, "error_report.json")
    s3_client.put_object(
        Bucket=output_bucket, Key=final_error_report_json_key, Body=json.dumps(error_report)
    )

    return error_report

def cascader(bucket_name, video_id):
    """
    Cascade compliance reports and make the final compliance report.
    The final report is stored as a dictionary.
    """
    # List all the files in the S3 path
    s3_client = boto3.client("s3", region_name="us-east-1")

    # Parse bucket name and prefix from s3_report_path
    prefix = os.path.join("shot_report/", video_id)

    # parsed_url = urlparse(bucket_name)
    # if parsed_url.scheme != "s3":
    #     raise ValueError(f"Invalid S3 URL scheme: {parsed_url.scheme}. Expected 's3'")

    # bucket = parsed_url.netloc
    generator_bucket = os.environ['GENERATOR_BUCKET_NAME']

    # List all objects in the bucket with the given prefix
    response = s3_client.list_objects_v2(Bucket=generator_bucket, Prefix=prefix) ## make sure all files are read
    

    final_report = {
        "scene_index": [],
        "frame_start_index": [],
        "frame_end_index": [],        
        "issue": [],
        "code": [],
        "explanation": [],
        "extended_explanation": [],
        #"recommendations": [],
        "confidence_score": [],
        "tier": []
    }

    # Check if there are any objects
    if "Contents" in response:
        # Loop through all JSON files
        for obj in response["Contents"]:
            key = obj["Key"]

            # Check if the file is a JSON file
            if key.endswith(".json"):
                scene_index = int(os.path.basename(key).split("_")[0])
                shot_index = int(os.path.basename(key).split("_")[1])
                # Get the file content
                file_obj = s3_client.get_object(Bucket=generator_bucket, Key=key)
                file_content = file_obj["Body"].read().decode("utf-8")

                # Parse JSON content
                report_dict = json.loads(file_content)
                # Store the dictionary into final_report
                # Assuming each report has the same structure as final_report
                for item in report_dict:
                    if item["frame_start_index"] != "-1":
                        final_report["scene_index"].append(scene_index)
                        for key, value in item.items():
                            final_report[key].append(value)

    # Sort final report based on scene_index and frame_start_index
    final_report = sorter(final_report)

    # Save it to a json file
    output_bucket = os.environ['BUCKET_NAME']
    final_report_json_key = os.path.join("final_report", video_id, "final_report.json")
    s3_client.put_object(
        Bucket=output_bucket, Key=final_report_json_key, Body=json.dumps(final_report)
    )

    # Save the dictionary as csv
    final_report_csv_key = os.path.join("final_report", video_id, "final_report.csv")
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    # Write headers
    headers = list(final_report.keys())
    writer.writerow(headers)
    # Write rows by zipping the values
    rows = zip(*final_report.values())
    writer.writerows(rows)
    s3_client.put_object(
        Bucket=output_bucket, Key=final_report_csv_key, Body=csv_buffer.getvalue()
    )

    # Error checker for missing generated compliance reports
    error_report = errorchecker(output_bucket, video_id)

    return final_report

def lambda_handler(event, context):
    # """Testing Event."""
    #event = {
    #            "bucket_name": "s3://vcp-unit-testing-05122025/",
    #            "video_id": "2U_ulXkfXqQ"
    #}

    # """AWS Lambda handler."""
    # logger.info(f"Lambda handler invoked with event: {json.dumps(event)}")
    # logger.info(f"Function name: {context.function_name}, remaining time: {context.get_remaining_time_in_millis()}ms")
    # video_bucket_path = event.get('bucket_name')
    video_bucket = os.environ['BUCKET_NAME']
    video_bucket_path = f"s3://{video_bucket}/"
    video_det = event.get('videoDetails')
    video_id = video_det.get('id') or video_det.get('folderName')

    try:
        logging.info("Cascade compliance reports......")
        final_report = cascader(video_bucket_path, video_id)
        logging.info("Done writing results!")
        return f"Success cascading compliance reports for {video_id} in bucket {video_bucket_path}!"

    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'status': 'FAILED'
            })
        }
