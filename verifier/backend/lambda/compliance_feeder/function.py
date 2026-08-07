import boto3
import logging
import os
import json

logger = logging.getLogger()
logger.setLevel(logging.INFO)

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

def lambda_handler(event, context):
    """
    Lists all S3 objects in a specified bucket and prefix, and returns the S3 URIs as a list.
    
    Parameters:
    - event: Should contain 'bucket' and 'prefix' keys
    - context: Lambda context
    
    Returns:
    - Dictionary containing statusCode and s3Uris list
    """
    try:
        # Extract bucket and prefix from the event
        bucket = os.environ['BUCKET_NAME']
        video_det = event.get('videoDetails')
        video_id = video_det['id']
        prefix = f"metadata/{video_det['id']}/"
        logger.info(f"Using prefix: {prefix}")
        # Validate input
        if not bucket:
            return {
                'statusCode': 400,
                'body': 'Missing required parameter: bucket'
            }
        
        # Initialize S3 client
         # Get shots information
        logger.info(f'Reading shots from: input_folder/{video_id}/{video_id}.mp4.shots.json')
        scenes = read_json_from_s3(bucket, f'input_folder/{video_id}/{video_id}.mp4.shots.json')
        
        # Create list of indices [0, 1, 2, ..., n-1] where n is the length of shots
        n = len(scenes)
        indices = list(range(n))

        # Return the list in the requested format
        return {
            'statusCode': 200,
            's3Uris': indices
        }
    
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': f'Error occurred: {str(e)}'
        }