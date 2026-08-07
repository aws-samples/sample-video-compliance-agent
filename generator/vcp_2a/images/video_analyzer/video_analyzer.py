#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
'''
Video Analyzer - ECS Container Implementation
Uses Bedrock Data Automation to analyze video processed by the Video Processor
'''

import boto3
import os
import json
import time
import logging
import sys
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class VideoAnalyzer:
    def __init__(self, output_bucket):
        self.S3_BUCKET_OUTPUT = output_bucket
        self.s3 = boto3.client("s3")
        self.bda_client = boto3.client('bedrock-data-automation', region_name='us-east-1')
        self.bda_runtime_client = boto3.client('bedrock-data-automation-runtime', region_name='us-east-1')
        self.MAX_POLLING_TIME = 7200  # 2 hours max polling time
        self.POLLING_INTERVAL = 30  # 30 seconds between status checks

    def get_aws_account_id(self):
        """Get the AWS account ID of the authenticated user."""
        sts_client = boto3.client('sts')
        return sts_client.get_caller_identity()["Account"]

    def read_json_from_s3(self, bucket_name, file_key):
        """Read a JSON file from S3 and return its contents as a Python dictionary"""
        try:
            logger.info(f"Reading JSON from s3://{bucket_name}/{file_key}")
            response = self.s3.get_object(
                Bucket=bucket_name,
                Key=file_key
            )
            
            json_content = response['Body'].read().decode('utf-8')
            data = json.loads(json_content)
            return data
            
        except Exception as e:
            logger.error(f"Error reading JSON from S3: {str(e)}")
            raise

    def parse_s3_uri(self, s3_uri):
        """Parse an S3 URI into bucket name and key"""
        path = s3_uri.replace('s3://', '')
        parts = path.split('/', 1)
        bucket_name = parts[0]
        file_key = parts[1] if len(parts) > 1 else ''
        return bucket_name, file_key

    def get_or_create_project(self):
        """Get existing project or create a new one"""
        try:
            logger.info("Creating Bedrock Data Automation project")
            response = self.bda_client.create_data_automation_project(
                projectName=f"video-analysis-job-{int(time.time())}",
                standardOutputConfiguration={
                    "video": {
                        "extraction": {
                            "category": {
                                "state": "ENABLED",
                                "types": ["CONTENT_MODERATION", "TEXT_DETECTION", "TRANSCRIPT"]
                            },
                            "boundingBox": {
                                "state": "DISABLED"
                            }
                        },
                        "generativeField": {
                            "state": "ENABLED",
                            "types": ["VIDEO_SUMMARY", "IAB", "CHAPTER_SUMMARY"]
                        }
                    }
                }
            )
            project_arn = response['projectArn']
            logger.info(f"Created new project with ARN: {project_arn}")
            return project_arn
        except ClientError as e:
            logger.error(f"Error creating project: {e}")
            raise

    def analyze_video(self, project_arn, video_s3_path):
        """Analyze a video using Bedrock Data Automation"""
        try:
            bucket_name, s3_key = self.parse_s3_uri(video_s3_path)
            logger.info(f"Starting analysis of video at s3://{bucket_name}/{s3_key}")

            input_config = {
                "s3Uri": f"s3://{bucket_name}/{s3_key}"
            }

            timestamp = int(time.time())
            output_config = {
                "s3Uri": f"s3://{bucket_name}/{s3_key}/metadata/metadata-output-{timestamp}"
            }
            
            logger.info(f"BDA output will be saved to {output_config['s3Uri']}")
    
            # Generate a client token for idempotency
            client_token = f"video-analysis-{timestamp}"

            account_id = self.get_aws_account_id()
    
            # Invoke with just the project ARN in the configuration
            response = self.bda_runtime_client.invoke_data_automation_async(
                clientToken=client_token,
                inputConfiguration=input_config,
                outputConfiguration=output_config,
                dataAutomationProfileArn=f"arn:aws:bedrock:us-east-1:{self.get_aws_account_id()}:data-automation-profile/us.data-automation-v1",
                dataAutomationConfiguration={
                    "dataAutomationProjectArn": project_arn,
                    "stage": "LIVE"
                }
            )
            
            # Get the invocation ARN from the response
            invocation_arn = response.get('invocationArn')
            logger.info(f"Started async job with invocation ARN: {invocation_arn}")
            
            if not invocation_arn:
                raise Exception("No invocation ARN received in response")
            
            # Poll for job completion with timeout logic
            start_time = time.time()
            while True:
                # Check if we've exceeded the maximum polling time
                elapsed_time = time.time() - start_time
                if elapsed_time > self.MAX_POLLING_TIME:
                    raise TimeoutError(f"Exceeded maximum polling time of {self.MAX_POLLING_TIME} seconds")
                
                status_response = self.bda_runtime_client.get_data_automation_status(
                    invocationArn=invocation_arn
                )
                
                status = status_response.get('status')
                logger.info(f"Job status: {status} - Elapsed time: {elapsed_time:.1f}s")
                
                if status == 'Success':
                    logger.info(f"Job completed successfully after {elapsed_time:.1f} seconds")
                    job_metadata_s3_uri = status_response["outputConfiguration"]['s3Uri']
                    
                    # Parse the S3 URI
                    bucket_name, file_key = self.parse_s3_uri(job_metadata_s3_uri)
                    
                    # Read and parse the JSON file
                    job_metadata = self.read_json_from_s3(bucket_name, file_key)
                    video_data_s3_uri = job_metadata['output_metadata'][0]['segment_metadata'][0]['standard_output_path']
                    
                    # Parse the S3 URI
                    bucket_name, file_key = self.parse_s3_uri(video_data_s3_uri)
                    
                    # Read and parse the JSON file
                    logger.info(f"Reading video metadata from {video_data_s3_uri}")
                    video_metadata = self.read_json_from_s3(bucket_name, file_key)
                    return video_metadata
    
                elif status in ['Failed', 'Cancelled', 'ServiceError']:
                    error_message = status_response.get('errorMessage', 'No error message provided')
                    error_code = status_response.get('errorCode', 'No error code provided')
                    raise Exception(f"Job failed with status: {status}, Error Code: {error_code}, Message: {error_message}")
                
                # Status is still in progress - wait before checking again
                logger.info(f"Job still in progress. Waiting {self.POLLING_INTERVAL} seconds before checking again...")
                time.sleep(self.POLLING_INTERVAL)
                
        except ClientError as e:
            logger.error(f"Error analyzing video: {e}")
            raise

    def save_analysis_to_s3(self, video_metadata, video_id, output_key_prefix=None):
        """
        Save analysis results to S3 using the pattern s3bucket/videofilename/video_analyzer_result.json
        """
        try:
            if output_key_prefix:
                # Make sure the output_key_prefix doesn't have trailing slashes
                output_key_prefix = output_key_prefix.rstrip('/')
            else:
                # If no prefix provided, just use the video_id
                output_key_prefix = video_id
                
            output_key = f"{output_key_prefix}/video_analyzer_result.json"
            
            logger.info(f"Saving analysis results to s3://{self.S3_BUCKET_OUTPUT}/{output_key}")

            json_content = json.dumps(video_metadata, indent=2)
            
            self.s3.put_object(
                Bucket=self.S3_BUCKET_OUTPUT,
                Key=output_key,
                Body=json_content,
                ContentType='application/json'
            )
            try:
                self.s3.head_object(Bucket=self.S3_BUCKET_OUTPUT, Key=output_key)
                logger.info(f"Successfully verified that file exists at s3://{self.S3_BUCKET_OUTPUT}/{output_key}")
            except Exception as e:
                logger.error(f"Failed to verify file was written: {e}")
                raise Exception(f"Failed to verify file was written to S3: {str(e)}")
                
            return f"s3://{self.S3_BUCKET_OUTPUT}/{output_key}"
        except Exception as e:
            logger.error(f"Error saving analysis to S3: {e}")
            raise

    def process_video(self, video_id):
        """Process a video that was processed by the Video Processor"""
        try:
            logger.info(f"Processing video ID: {video_id}")
    
            # Use the original input video (with audio) so BDA can extract transcripts
            video_file_key = f"input/{video_id}.mp4"
            video_s3_path = f"s3://{self.S3_BUCKET_OUTPUT}/{video_file_key}"

            logger.info(f"Analyzing original input video at {video_s3_path}")
    
            # Create BDA project
            project_arn = self.get_or_create_project()
            logger.info(f"Using project with ARN: {project_arn}")
    
            # Start analysis and wait for completion
            logger.info("Starting video analysis with Bedrock Data Automation...")
            logger.info("This will take several minutes to complete...")
            video_metadata = self.analyze_video(project_arn, video_s3_path)
            logger.info("BDA analysis complete! Processing results...")

            # Save the results to the correct path format: videofilename/video_analyzer_result.json
            output_path = self.save_analysis_to_s3(video_metadata, video_id)
            logger.info(f"Analysis complete! Results saved to {output_path}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Error processing video: {e}")
            raise

def main():
    """Container entrypoint function"""
    logger.info("Starting video analysis task")

    try:
        # Get environment variables
        output_bucket = os.environ.get('BUCKET_NAME') or os.environ.get('OUTPUT_BUCKET_NAME')
        video_s3_key = os.environ.get('VIDEO_S3_KEY')
        
        if not output_bucket:
            logger.error("Missing BUCKET_NAME or OUTPUT_BUCKET_NAME environment variable")
            sys.exit(1)
            
        if not video_s3_key:
            logger.error("Missing VIDEO_S3_KEY environment variable")
            sys.exit(1)
            
        logger.info(f"Configuration: output_bucket={output_bucket}, video_key={video_s3_key}")
        
        # Extract video ID from the video key
        # The standard format is path/to/video/filename.mp4
        # We want just the filename without extension
        video_id = os.path.splitext(os.path.basename(video_s3_key))[0]
        logger.info(f"Extracted video ID: {video_id}")
        
        # Initialize the analyzer
        analyzer = VideoAnalyzer(output_bucket)
        
        # Process the video - this will block until the BDA job completes
        logger.info(f"Starting video analysis for video ID: {video_id}")
        output_path = analyzer.process_video(video_id)
        
        logger.info(f"Video analysis complete. Output saved to: {output_path}")
        logger.info("ECS task completed successfully")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error analyzing video: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()