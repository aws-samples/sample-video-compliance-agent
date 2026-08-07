#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
import os
import json
import boto3
import cv2
import re
import subprocess
import shutil
import logging
import sys
import tempfile

# Configure logging for ECS - make sure logs are flushed immediately
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

_SAFE_S3_KEY_RE = re.compile(r'^[\w\-./]+$')

def _validate_s3_key(s3_key: str) -> str:
    """Reject S3 keys that contain path traversal or shell metacharacters."""
    if not s3_key or not _SAFE_S3_KEY_RE.match(s3_key):
        raise ValueError(f"Unsafe S3 key rejected: {s3_key!r}")
    if ".." in s3_key.split("/"):
        raise ValueError(f"Path traversal in S3 key rejected: {s3_key!r}")
    return s3_key

def _assert_path_within(path: str, directory: str) -> str:
    """Raise if resolved path is outside the expected directory."""
    resolved = os.path.realpath(path)
    if not resolved.startswith(os.path.realpath(directory) + os.sep) and resolved != os.path.realpath(directory):
        raise ValueError(f"Path escapes expected directory: {path!r}")
    return resolved

class VideoProcessor:
    def __init__(self, bucket):
        logger.info("=== Initializing VideoProcessor ===")
        self.S3_BUCKET = bucket  # Single bucket for both input and output
        logger.info(f"S3 Bucket: {self.S3_BUCKET}")

        self.s3 = boto3.client("s3")
        logger.info(f"Boto3 S3 client created")
        logger.info(f"Boto3 version: {boto3.__version__}")

        # Log AWS credentials status (without exposing actual credentials)
        try:
            sts = boto3.client('sts')
            identity = sts.get_caller_identity()
            logger.info(f"AWS Identity - Account: {identity['Account']}, ARN: {identity['Arn']}")
        except Exception as e:
            logger.error(f"Failed to get AWS identity: {e}")

        # Lambda paths for ffmpeg and ffprobe from our custom layer
        self.FFMPEG_PATH = "/usr/local/bin/ffmpeg"
        self.FFPROBE_PATH = "/usr/local/bin/ffprobe"
        logger.info(f"FFmpeg path: {self.FFMPEG_PATH}")
        logger.info(f"FFprobe path: {self.FFPROBE_PATH}")

        # Ensure tmp directory exists
        logger.info("Temp directory will be created on demand")
        logger.info("=== VideoProcessor initialization complete ===\n")

    def download_video_from_s3(self, s3_key, local_filename):
        logger.info(f"Starting S3 download")
        logger.info(f"  S3 bucket: {self.S3_BUCKET}")
        logger.info(f"  S3 key: {s3_key}")
        logger.info(f"  Local destination: {local_filename}")

        try:
            self.s3.download_file(self.S3_BUCKET, s3_key, local_filename)
            file_size = os.path.getsize(local_filename)
            logger.info(f"✓ Successfully downloaded {s3_key}")
            logger.info(f"  Downloaded file size: {file_size} bytes")
            logger.info(f"  Local path: {local_filename}")
        except Exception as e:
            logger.error(f"✗ Failed to download from S3")
            logger.error(f"  Bucket: {self.S3_BUCKET}")
            logger.error(f"  Key: {s3_key}")
            logger.error(f"  Error type: {type(e).__name__}")
            logger.error(f"  Error message: {str(e)}", exc_info=True)
            raise

    def process_video(self, video_path):
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        parent_folder = tempfile.mkdtemp(prefix=f"{video_name}_")
        video_folder = f"{parent_folder}/video"
        audio_folder = f"{parent_folder}/audio"
        frames_folder = f"{parent_folder}/selected_frames"
        metadata_folder = f"{parent_folder}/metadata"
        
        # Create directories
        os.makedirs(video_folder, exist_ok=True)
        os.makedirs(audio_folder, exist_ok=True)
        os.makedirs(frames_folder, exist_ok=True)
        os.makedirs(metadata_folder, exist_ok=True)

        # Separate video and audio
        video_output = f"{video_folder}/{video_name}.mp4"
        audio_output = f"{audio_folder}/{video_name}.mp3"
        
        safe_video_path = _assert_path_within(video_path, tempfile.gettempdir())
        safe_video_output = _assert_path_within(video_output, parent_folder)
        safe_audio_output = _assert_path_within(audio_output, parent_folder)

        logger.info(f"Extracting video without audio to {video_output}")
        subprocess.run(
            [self.FFMPEG_PATH, "-i", safe_video_path, "-an", "-c:v", "copy", safe_video_output],
            check=True
        )

        logger.info(f"Extracting audio to {audio_output}")
        subprocess.run(
            [self.FFMPEG_PATH, "-i", safe_video_path, "-vn", safe_audio_output],
            check=True
        )

        # Extract metadata
        metadata_output = f"{metadata_folder}/{video_name}.json"
        safe_metadata_output = _assert_path_within(metadata_output, parent_folder)
        logger.info(f"Extracting metadata to {metadata_output}")
        cmd = [self.FFPROBE_PATH, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", safe_video_path]
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        metadata = json.loads(result.stdout)
        with open(safe_metadata_output, "w") as f:
            json.dump(metadata, f, indent=4)

        # Extract frames
        logger.info(f"Extracting frames to {frames_folder}")
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"Error: Cannot open video file {video_path}")
            return parent_folder

        fps = int(cap.get(cv2.CAP_PROP_FPS))
        if fps == 0:
            logger.error("Error: FPS is zero, cannot process video.")
            return parent_folder

        frame_rate = 2  # Extract 2 frames per second
        frame_interval = int(fps / frame_rate)
        frame_count = 0
        saved_count = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            if frame_count % frame_interval == 0:
                frame_filename = f"{frames_folder}/frame_{frame_count:04d}.jpg"
                cv2.imwrite(frame_filename, frame)
                saved_count += 1
            frame_count += 1

        cap.release()
        logger.info(f"Processing completed for {video_name}, extracted {saved_count} frames")
        return parent_folder

    def upload_results_to_s3(self, local_folder, s3_video_key):
        # Get the filename without extension
        filename = os.path.splitext(os.path.basename(s3_video_key))[0]
        # Create the base output path
        base_output_path = f"{filename}/"

        logger.info(f"Starting S3 upload process")
        logger.info(f"  Local folder: {local_folder}")
        logger.info(f"  S3 bucket: {self.S3_BUCKET}")
        logger.info(f"  Base output path: {base_output_path}")
        logger.info(f"  Original S3 video key: {s3_video_key}")

        # Check if local folder exists
        if not os.path.exists(local_folder):
            logger.error(f"Local folder does not exist: {local_folder}")
            raise FileNotFoundError(f"Local folder not found: {local_folder}")

        # Count files to upload
        file_count = 0
        for root, _, files in os.walk(local_folder):
            file_count += len(files)
        logger.info(f"Found {file_count} files to upload")

        uploaded_count = 0
        for root, _, files in os.walk(local_folder):
            for file in files:
                full_path = os.path.join(root, file)
                # Construct S3 key maintaining the folder structure (audio, video, frames, metadata)
                relative_path = os.path.relpath(full_path, local_folder)
                s3_key = f"{base_output_path}{relative_path}"

                try:
                    file_size = os.path.getsize(full_path)
                    logger.info(f"Uploading file {uploaded_count + 1}/{file_count}:")
                    logger.info(f"  Local path: {full_path}")
                    logger.info(f"  File size: {file_size} bytes")
                    logger.info(f"  S3 key: {s3_key}")

                    self.s3.upload_file(full_path, self.S3_BUCKET, s3_key)
                    uploaded_count += 1
                    logger.info(f"  ✓ Successfully uploaded to s3://{self.S3_BUCKET}/{s3_key}")

                except Exception as e:
                    logger.error(f"  ✗ Failed to upload {full_path}")
                    logger.error(f"  Target: s3://{self.S3_BUCKET}/{s3_key}")
                    logger.error(f"  Error type: {type(e).__name__}")
                    logger.error(f"  Error message: {str(e)}", exc_info=True)
                    raise

        logger.info(f"Upload complete: {uploaded_count}/{file_count} files uploaded successfully")
        logger.info(f"All files uploaded to: s3://{self.S3_BUCKET}/{base_output_path}/")
        return f"s3://{self.S3_BUCKET}/{base_output_path}/"

    def process_single_video(self, s3_video_key):
        _validate_s3_key(s3_video_key)
        logger.info("=" * 80)
        logger.info(f"=== STARTING VIDEO PROCESSING ===")
        logger.info(f"S3 Video Key: {s3_video_key}")
        temp_dir = tempfile.mkdtemp(prefix="video_download_")
        local_video_path = os.path.join(temp_dir, os.path.basename(s3_video_key))
        logger.info(f"Local Video Path: {local_video_path}")
        logger.info("=" * 80)

        try:
            # Download video from S3
            logger.info("\n--- STEP 1: DOWNLOADING VIDEO FROM S3 ---")
            self.download_video_from_s3(s3_video_key, local_video_path)
            logger.info("✓ Download step completed\n")

            # Process video
            logger.info("--- STEP 2: PROCESSING VIDEO ---")
            output_folder = self.process_video(local_video_path)
            logger.info(f"✓ Processing step completed. Output folder: {output_folder}\n")

            # Upload results back to S3
            if output_folder:
                logger.info("--- STEP 3: UPLOADING RESULTS TO S3 ---")
                output_path = self.upload_results_to_s3(output_folder, s3_video_key)
                logger.info(f"✓ Upload step completed\n")
                logger.info("=" * 80)
                logger.info(f"=== VIDEO PROCESSING COMPLETED SUCCESSFULLY ===")
                logger.info(f"Output location: {output_path}")
                logger.info("=" * 80)
                return output_path
            else:
                logger.error("✗ Processing did not produce an output folder")
                raise Exception("Video processing failed - no output folder generated")

        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"=== VIDEO PROCESSING FAILED ===")
            logger.error(f"Video: {s3_video_key}")
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Error message: {str(e)}")
            logger.error("=" * 80, exc_info=True)
            raise
        finally:
            # Cleanup temp directories
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    logger.info(f"Cleaned up download temp directory: {temp_dir}")
                
                if 'output_folder' in locals() and output_folder and os.path.exists(output_folder):
                    shutil.rmtree(output_folder)
                    logger.info(f"Cleaned up processing temp directory: {output_folder}")
                
                logger.info("Cleanup complete")
            except Exception as e:
                logger.warning(f"Error during cleanup: {str(e)}")

def main():
    """Container entrypoint function"""
    logger.info("Starting video processing task")
    
    # Get configuration from environment variables
    bucket = os.environ.get('BUCKET_NAME')
    s3_video_key = os.environ.get('VIDEO_S3_KEY')
    
    logger.info(f"Configuration: bucket={bucket}, video_key={s3_video_key}")
    
    if not all([bucket, s3_video_key]):
        logger.error("Missing required environment variables")
        sys.exit(1)
    
    try:
        # Test if FFmpeg is available
        if not os.path.exists('/usr/local/bin/ffmpeg'):
            logger.error("FFmpeg not found")
            sys.exit(1)
            
        processor = VideoProcessor(bucket)  # Pass single bucket
        output_path = processor.process_single_video(s3_video_key)
        logger.info(f"Processing complete. Output at: {output_path}")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error processing video: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()