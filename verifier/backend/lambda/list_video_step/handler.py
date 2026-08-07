import os
import boto3
from typing import List, Dict

def parse_s3_uri(s3uri: str) -> tuple[str, str]:
    """
    Parse s3uri of format s3://bucket-name/folder/path/ into bucket and key
    """
    # Remove 's3://' prefix
    path = s3uri.replace('s3://', '')
    # Split into bucket and key
    parts = path.split('/', 1)
    bucket = parts[0]
    # If there's no path after bucket, use empty string
    key = parts[1] if len(parts) > 1 else ''
    return bucket, key

def lambda_handler(event, context) -> Dict:
    """
    Lists all folders in the specified S3 location.
    
    Expected event format:
    {
        "s3uri": "s3://bucketname/folder/"  # For batch processing
    }
    OR (when triggered by DynamoDB):
    {
        "videosList": {"videos": [{"id": "...", "s3Uri": "..."}]}
    }
    """
    # If already has videosList (from DynamoDB trigger), pass through
    if 'videosList' in event:
        return event['videosList']
    
    s3uri = event['s3uri']
    bucket_name, folder_path = parse_s3_uri(s3uri)
    
    # Initialize S3 client
    s3_client = boto3.client('s3')
    
    # List folders in the location
    try:
        folders = []
        paginator = s3_client.get_paginator('list_objects_v2')
        
        # Handle pagination for locations with many folders
        for page in paginator.paginate(
            Bucket=bucket_name,
            Prefix=folder_path,
            Delimiter='/'  # Use delimiter to get folders only
        ):
            # Process common prefixes (folders)
            if 'CommonPrefixes' in page:
                for prefix_obj in page['CommonPrefixes']:
                    prefix = prefix_obj['Prefix']
                    
                    # Extract folder name from prefix
                    # Remove the base prefix to get relative path
                    relative_path = prefix
                    if folder_path and prefix.startswith(folder_path):
                        relative_path = prefix[len(folder_path):]
                    
                    # The folder name is the first segment of the relative path
                    folder_name = relative_path.split('/')[0] if relative_path else ''
                    
                    if folder_name:  # Only add if there's a folder name
                        folders.append({
                            'folderName': folder_name,
                            'prefix': prefix,
                            's3Uri': f"s3://{bucket_name}/{prefix}",
                            'id': folder_name  # Adding id field as needed by your process
                        })
        
        return {
            "videos": folders,
            "totalCount": len(folders)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'error': str(e)
        }