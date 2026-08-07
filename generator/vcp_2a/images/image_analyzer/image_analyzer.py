#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
import boto3
import json
import logging
import time
import random
import os
import signal
import sys
from typing import Dict, List
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor, as_completed


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

running = True
def signal_handler(sig, frame):
    global running
    logger.info(f"Received signal {sig}. Beginning graceful shutdown...")
    running = False
    # Give time for current operations to complete
    logger.info("Will exit after current operation completes.")

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

class S3Utility:
    """S3 utility class to handle S3 operations"""
    def __init__(self, region_name='us-east-1'):
        self.s3_client = boto3.client('s3', region_name=region_name)
        self.logger = logging.getLogger(__name__ + ".S3Utility")
        
    def parse_s3_uri(self, s3_uri):
        """Parse an S3 URI into bucket name and key"""
        path = s3_uri.replace('s3://', '')
        parts = path.split('/', 1)
        bucket_name = parts[0]
        file_key = parts[1] if len(parts) > 1 else ''
        return bucket_name, file_key
        
    def list_files(self, bucket_name, prefix='', filter_suffix=None):
        """List files in an S3 bucket with optional filtering"""
        try:
            result = []
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        # Filter by suffix if provided
                        if filter_suffix is None or key.lower().endswith(filter_suffix.lower()):
                            # Only include files, not directories
                            if not key.endswith('/'):
                                result.append(key)
            
            return result
        except Exception as e:
            self.logger.error(f"Error listing files from S3: {e}")
            raise
            
    def read_json_from_s3(self, bucket_name, file_key):
        """Read a JSON file from S3 and return its contents as a Python dictionary"""
        try:
            self.logger.info(f"Reading JSON from s3://{bucket_name}/{file_key}")
            
            response = self.s3_client.get_object(
                Bucket=bucket_name,
                Key=file_key
            )
            
            json_content = response['Body'].read().decode('utf-8')
            data = json.loads(json_content)
            
            return data
            
        except Exception as e:
            self.logger.error(f"Error reading JSON from S3: {e}")
            raise
            
    def write_json_to_s3(self, data, bucket_name, file_key):
        """Write JSON data to S3"""
        try:
            self.logger.info(f"Writing JSON to s3://{bucket_name}/{file_key}")
            
            # Convert data to JSON string with pretty formatting
            json_content = json.dumps(data, indent=2)
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key=file_key,
                Body=json_content,
                ContentType='application/json'
            )
            
            # Verify the file was written successfully
            try:
                self.s3_client.head_object(Bucket=bucket_name, Key=file_key)
                self.logger.info(f"Successfully verified that file exists at s3://{bucket_name}/{file_key}")
            except Exception as e:
                self.logger.error(f"Failed to verify file was written: {e}")
                raise Exception(f"Failed to verify file was written to S3: {str(e)}")
                
            return f"s3://{bucket_name}/{file_key}"
            
        except Exception as e:
            self.logger.error(f"Error writing JSON to S3: {e}")
            raise

class ImageAnalyzer:
    def __init__(self, region_name: str = 'us-east-1'):
        """
        Initialize the ImageAnalyzer
        
        Args:
            region_name: AWS region name (default: us-east-1)
        """
        self.s3_util = S3Utility(region_name)
        self.bda_client = boto3.client('bedrock-data-automation', region_name=region_name)
        self.bda_runtime_client = boto3.client('bedrock-data-automation-runtime', region_name=region_name)
        self.sts_client = boto3.client('sts', region_name=region_name)
        self.logger = logging.getLogger(__name__)
        # Get AWS account ID once at initialization to avoid repeated calls
        self.account_id = self.get_aws_account_id()
        self.logger.info(f"Initialized ImageAnalyzer with AWS Account ID: {self.account_id}")
        self.MAX_POLLING_TIME = 7200  # 2 hours max polling time
        self.POLLING_INTERVAL = 15

    def get_aws_account_id(self) -> str:
        """Get the AWS account ID of the authenticated user."""
        try:
            return self.sts_client.get_caller_identity()["Account"]
        except ClientError as e:
            self.logger.error(f"Error retrieving AWS account ID: {e}", exc_info=True)
            raise

    def get_or_create_project(self):
        """Get existing project or create a new one"""
        try:
            self.logger.info("Creating new BDA project")
            response = self.bda_client.create_data_automation_project(
                projectName=f"image-analysis-job-{int(time.time())}",
                standardOutputConfiguration={
                    "image": {
                        "extraction": {
                            "category": {
                                "state": "ENABLED",
                                "types": ["CONTENT_MODERATION","TEXT_DETECTION"]
                            },
                            "boundingBox": {
                                "state": "ENABLED"
                            }
                        },
                        "generativeField": {
                            "state": "ENABLED",
                            "types": ["IMAGE_SUMMARY","IAB"]
                        }
                    }
                }
            )
            self.logger.info(f"Created new project with ARN: {response['projectArn']}")
            return response['projectArn']
        except ClientError as e:
            self.logger.error(f"Error creating project: {e}", exc_info=True)
            raise

    def analyze_image(self, project_arn: str, image_s3_path: str):
        """Analyze an image using BDA"""
        try:
            self.logger.info(f"Starting image analysis for {image_s3_path}")
            bucket_name, s3_key = self.s3_util.parse_s3_uri(image_s3_path)

            input_config = {
                "s3Uri": f"s3://{bucket_name}/{s3_key}"
            }

            timestamp = int(time.time())
            output_config = {
                "s3Uri": f"s3://{bucket_name}/{s3_key}/metadata/image-metadata-output-{timestamp}"
            }
            
            client_token = f"image-analysis-{timestamp}-{random.randint(1000, 9999)}"

            self.logger.info("Invoking BDA async job")
            response = self.bda_runtime_client.invoke_data_automation_async(
                clientToken=client_token,
                inputConfiguration=input_config,
                outputConfiguration=output_config,
                dataAutomationProfileArn=f"arn:aws:bedrock:us-east-1:{self.account_id}:data-automation-profile/us.data-automation-v1",
                dataAutomationConfiguration={
                    "dataAutomationProjectArn": project_arn,
                    "stage": "LIVE"
                }
            )
            
            invocation_arn = response.get('invocationArn')
            self.logger.info(f"Started async job with invocation ARN: {invocation_arn}")
            
            if not invocation_arn:
                self.logger.error("No invocation ARN received in response")
                raise Exception("No invocation ARN received in response")
            
            # Poll for job completion with timeout logic
            start_time = time.time()
            while True:
                # Check if we should continue or gracefully shut down
                global running
                if not running:
                    self.logger.info("Graceful shutdown requested during BDA job monitoring")
                    raise InterruptedError("Analysis interrupted by shutdown request")
                    
                # Check if we've exceeded the maximum polling time
                elapsed_time = time.time() - start_time
                if elapsed_time > self.MAX_POLLING_TIME:
                    self.logger.error(f"Exceeded maximum polling time of {self.MAX_POLLING_TIME} seconds")
                    raise TimeoutError(f"Exceeded maximum polling time of {self.MAX_POLLING_TIME} seconds")
                
                status_response = self.bda_runtime_client.get_data_automation_status(
                    invocationArn=invocation_arn
                )
                
                status = status_response.get('status')
                self.logger.info(f"Job status: {status} - Elapsed time: {elapsed_time:.1f}s")
                
                if status == 'Success':
                    self.logger.info(f"Job completed successfully after {elapsed_time:.1f} seconds")
                    job_metadata_s3_uri = status_response["outputConfiguration"]['s3Uri']
                    bucket_name, file_key = self.s3_util.parse_s3_uri(job_metadata_s3_uri)
                    
                    self.logger.info("Reading job metadata from S3")
                    job_metadata = self.s3_util.read_json_from_s3(bucket_name, file_key)
                    
                    image_data_s3_uri = job_metadata['output_metadata'][0]['segment_metadata'][0]['standard_output_path']
                    bucket_name, file_key = self.s3_util.parse_s3_uri(image_data_s3_uri)
                    
                    self.logger.info("Reading image metadata from S3")
                    image_metadata = self.s3_util.read_json_from_s3(bucket_name, file_key)
                    return image_metadata
                    
                elif status in ['Failed', 'Cancelled', 'ServiceError']:
                    error_message = status_response.get('errorMessage', 'No error message provided')
                    error_code = status_response.get('errorCode', 'No error code provided')
                    self.logger.error(f"Job failed - Status: {status}, Error Code: {error_code}, Message: {error_message}")
                    raise Exception(f"Job failed with status: {status}, Error Code: {error_code}, Message: {error_message}")
                
                self.logger.info(f"Job still in progress. Waiting {self.POLLING_INTERVAL} seconds before checking again...")
                time.sleep(self.POLLING_INTERVAL)
                
        except ClientError as e:
            self.logger.error(f"Error analyzing image: {e}", exc_info=True)
            raise

    def analyze_images_in_folder(self, bucket_name: str, video_id: str, frames_folder: str = None) -> str:
        """
        Analyze all images in a specified S3 folder and return the results
        
        Args:
            bucket_name: S3 bucket name
            video_id: Video ID used as the prefix for the output file
            frames_folder: S3 folder path containing images to analyze (default: <video_id>/selected_frames)
        
        Returns:
            S3 path to the JSON file containing analysis results
        """
        try:
            # If no frames_folder provided, use the phash_selected_frames structure
            if not frames_folder:
                frames_folder = f"{video_id}/phash_selected_frames"
            
            self.logger.info(f"Processing images in bucket: {bucket_name}, folder: {frames_folder}")
            
            # List all images in the folder
            image_extensions = ['.jpg', '.jpeg', '.png']
            all_files = []
            
            for ext in image_extensions:
                self.logger.info(f"Listing files with extension {ext}")
                files = self.s3_util.list_files(bucket_name, prefix=frames_folder, filter_suffix=ext)
                all_files.extend(files)
            
            # Remove duplicates and sort
            images = sorted(list(set(all_files)))
            
            if not images:
                self.logger.warning(f"No images found in folder: {frames_folder}")
                return None
            
            self.logger.info(f"Found {len(images)} images to analyze")
            
            # Create a single project for all images
            project_arn = self.get_or_create_project()
            self.logger.info(f"Using project with ARN: {project_arn}")
            
            # Define a worker function for processing a single image
            def process_image(image_key):
                try:
                    # Check if we should continue or gracefully shut down
                    if not running:
                        self.logger.info(f"Graceful shutdown requested while processing {image_key}")
                        return None
                    
                    image_s3_path = f"s3://{bucket_name}/{image_key}"
                    self.logger.info(f"Processing image: {image_key}")
                    image_metadata = self.analyze_image(project_arn, image_s3_path)
                    
                    # Add source image information
                    image_metadata['source_image'] = image_key
                    return image_metadata
                except Exception as e:
                    self.logger.error(f"Error processing image {image_key}: {str(e)}", exc_info=True)
                    return None
            
            # Process images in parallel
            analysis_results = []
            completed_count = 0
            total_images = len(images)
            
            # Use a lock for thread-safe operations on shared resources
            from threading import Lock
            save_lock = Lock()
            
            # Configure the number of parallel workers
            # This can be adjusted based on resource constraints and API limits
            max_workers = min(5, total_images)  # Default to 5 or fewer if there are fewer images
            
            # Get from environment variable if available
            env_max_workers = os.environ.get('MAX_PARALLEL_IMAGES')
            if env_max_workers and env_max_workers.isdigit():
                max_workers = min(int(env_max_workers), total_images)
            
            self.logger.info(f"Processing images with {max_workers} parallel workers")
            
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_image = {executor.submit(process_image, image_key): image_key for image_key in images}
                
                # Process results as they complete
                for future in as_completed(future_to_image):
                    image_key = future_to_image[future]
                    try:
                        result = future.result()
                        if result:
                            with save_lock:
                                analysis_results.append(result)
                                completed_count += 1
                                
                                # Save intermediate results periodically
                                if completed_count % 10 == 0 or completed_count == total_images:
                                    self.logger.info(f"Saving intermediate results after processing {completed_count}/{total_images} images")
                                    intermediate_key = f"{video_id}/metadata/image_analyzer_intermediate_{int(time.time())}.json"
                                    self.s3_util.write_json_to_s3(analysis_results, bucket_name, intermediate_key)
                        else:
                            self.logger.warning(f"No result returned for image {image_key}")
                            
                    except Exception as e:
                        self.logger.error(f"Exception processing future for {image_key}: {e}", exc_info=True)
            
            # Check if we need to handle graceful shutdown
            if not running:
                self.logger.info("Graceful shutdown requested. Saving progress before exit...")
                # Save what we have so far
                output_key = f"{video_id}/image_analyzer_result_partial_{int(time.time())}.json"
                self.s3_util.write_json_to_s3(analysis_results, bucket_name, output_key)
                return f"s3://{bucket_name}/{output_key}"
            
            # Save final results to the required format: videofilename/image_analyzer_result.json
            output_key = f"{video_id}/image_analyzer_result.json"
            self.s3_util.write_json_to_s3(analysis_results, bucket_name, output_key)
            
            output_path = f"s3://{bucket_name}/{output_key}"
            self.logger.info(f"Analysis complete. Processed {len(analysis_results)}/{total_images} images. Results saved to: {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"Error processing images: {str(e)}", exc_info=True)
            raise


def main():
    """Container entrypoint function for image analysis"""
    logger.info("Starting image analysis task")

    output_bucket = os.environ.get('BUCKET_NAME') or os.environ.get('OUTPUT_BUCKET_NAME')
    video_s3_key = os.environ.get('VIDEO_S3_KEY')
    region_name = os.environ.get('AWS_REGION', 'us-east-1')
    
    logger.info(f"Configuration: bucket={output_bucket}, video_key={video_s3_key}, region={region_name}")
    
    if not output_bucket:
        logger.error("Missing BUCKET_NAME or OUTPUT_BUCKET_NAME environment variable")
        sys.exit(1)
        
    if not video_s3_key:
        logger.error("Missing VIDEO_S3_KEY environment variable")
        sys.exit(1)
    
    try:
        # Extract the video ID from the original video key
        video_id = os.path.splitext(os.path.basename(video_s3_key))[0]
        logger.info(f"Extracted video ID: {video_id}")
        
        # Initialize the analyzer with the correct region
        analyzer = ImageAnalyzer(region_name=region_name)
        
        # Process all images in the frames folder
        frames_folder = f"{video_id}/phash_selected_frames"
        logger.info(f"Processing images in folder: {frames_folder}")
        output_path = analyzer.analyze_images_in_folder(output_bucket, video_id, frames_folder)
        
        if output_path:
            logger.info(f"Image analysis complete. Output at: {output_path}")
            logger.info("All image analysis jobs have completed successfully. Container will now exit.")
            sys.exit(0)
        else:
            logger.error("No images were found to analyze")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error analyzing images: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()