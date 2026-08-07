import base64
import json
import logging
import re
import os
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    try:
        # Extract bucket_name and video_id from the input event
        s3_uri = event['videoDetails']['s3Uri']
        video_id = event['videoDetails']['id']
        bucket_name = s3_uri.split('/')[2]
        
        logger.info(f"Processing video ID: {video_id} in bucket: {bucket_name}")
             
        # Get shots information
        scenes = read_json_from_s3(bucket_name, f'input_folder/{video_id}/{video_id}.mp4.shots.json')
        
        # Create list of indices [0, 1, 2, ..., n-1] where n is the length of scenes
        n = len(scenes)
        indices = list(range(n))
        
        return {
            'statusCode': 200,
            'shot_index': indices
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error processing video: {str(e)}')
        }

def read_json_from_s3(bucket_name, file_key):
    """
    Read a JSON file from S3 and return its contents as a Python dictionary
    """
    try:
        # Create an S3 client
        s3_client = boto3.client('s3')
        
        # Get the object from S3
        response = s3_client.get_object(
            Bucket=bucket_name,
            Key=file_key
        )
        
        # Read the JSON content
        json_content = response['Body'].read().decode('utf-8')
        
        # Parse JSON into a Python dictionary
        data = json.loads(json_content)
        
        return data
        
    except Exception as e:
        logger.error(f"Error reading JSON from S3: {str(e)}")
        raise
