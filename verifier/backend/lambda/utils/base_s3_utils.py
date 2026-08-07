import os
import boto3
import json
from typing import Dict, List, Union, Any
from botocore.exceptions import ClientError

class S3Utility:
    def __init__(self, region_name: str = os.environ.get('AWS_REGION', 'us-east-1')):
        self.s3_client = boto3.client('s3', region_name=region_name)
    
    def parse_s3_uri(self, s3_uri: str) -> tuple:
        """Parse an S3 URI into bucket name and key"""
        path = s3_uri.replace('s3://', '')
        parts = path.split('/', 1)
        bucket_name = parts[0]
        file_key = parts[1] if len(parts) > 1 else ''
        return bucket_name, file_key

    def read_json_from_s3(self, bucket_name: str, file_key: str) -> Dict:
        """Read and parse JSON file from S3"""
        try:
            response = self.s3_client.get_object(Bucket=bucket_name, Key=file_key)
            json_content = response['Body'].read().decode('utf-8')
            return json.loads(json_content)
        except ClientError as e:
            raise Exception(f"Error reading JSON from S3: {str(e)}")

    def write_json_to_s3(self, data: Dict, bucket_name: str, file_key: str) -> bool:
        """Write JSON data to S3"""
        try:
            json_data = json.dumps(data)
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key=file_key,
                Body=json_data
            )
            return True
        except ClientError as e:
            raise Exception(f"Error writing JSON to S3: {str(e)}")

    def list_files(self, bucket_name: str, prefix: str = '') -> List[str]:
        """List files in S3 bucket with optional prefix"""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=prefix
            )
            files = []
            if 'Contents' in response:
                files = [obj['Key'] for obj in response['Contents']]
            return files
        except ClientError as e:
            raise Exception(f"Error listing S3 files: {str(e)}")

    def upload_file(self, file_obj: Any, bucket_name: str, file_key: str) -> bool:
        """Upload file object to S3"""
        try:
            self.s3_client.upload_fileobj(file_obj, bucket_name, file_key)
            return True
        except ClientError as e:
            raise Exception(f"Error uploading file to S3: {str(e)}")

    def download_file(self, bucket_name: str, file_key: str, local_path: str) -> bool:
        """Download file from S3 to local path"""
        try:
            self.s3_client.download_file(bucket_name, file_key, local_path)
            return True
        except ClientError as e:
            raise Exception(f"Error downloading file from S3: {str(e)}")

    def file_exists(self, bucket_name: str, file_key: str) -> bool:
        """Check if file exists in S3"""
        try:
            self.s3_client.head_object(Bucket=bucket_name, Key=file_key)
            return True
        except ClientError:
            return False