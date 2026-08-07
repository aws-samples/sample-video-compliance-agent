#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
import base64
import json
import re
import os
import boto3
import sys
import logging
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MetadataProcessor:
    def __init__(self):
        self.bucket_name = os.environ.get('INPUT_BUCKET_NAME')
        self.output_bucket = os.environ.get('OUTPUT_BUCKET_NAME', self.bucket_name)  # Use INPUT_BUCKET as default
        
        if not self.bucket_name:
            raise ValueError("INPUT_BUCKET_NAME environment variable not set")
        
        self.s3_client = boto3.client('s3')
    
    def process_metadata(self, video_id, shot_index=None):
        """Main function to process metadata for all shots (or a specific shot) in a video"""
        try:
            logger.info(f"Processing video ID: {video_id} in bucket: {self.bucket_name}")

            # Preprocess analyzer output to create expected files
            self.preprocess_analyzer_output(video_id)

            # Get video metadata
            metadata = self.read_json_from_s3(f'input_folder/{video_id}/{video_id}.mp4.metadata.json')
            if not metadata:
                logger.warning("Metadata file not found, using default values")
                metadata = {"streams": [{"r_frame_rate": "24/1"}]}

            # Get shots information
            shots = self.read_json_from_s3(f'input_folder/{video_id}/{video_id}.mp4.shots.json')
            if not shots:
                logger.warning("Shots file not found, using default values")
                shots = [{
                    "shot_id": 0,
                    "content": "",
                    "start_frame": 0,
                    "end_frame": 100,
                    "start_time": 0,
                    "end_time": 10,
                    "confidence": 1.0
                }]

            # Get scenes information
            scenes = self.read_json_from_s3(f'input_folder/{video_id}/{video_id}.mp4.scenes.json')
            if not scenes:
                logger.warning("Scenes file not found, using empty list")
                scenes = []

            # Get frames description
            frames = self.read_json_from_s3(f'input_folder/{video_id}/{video_id}.mp4.frames.json')
            if not frames:
                logger.warning("Frames file not found, using empty list")
                frames = []

            # Get OCR data
            ocr = self.read_json_from_s3(f'input_folder/{video_id}/{video_id}.merged_ocr_results.json')
            if not ocr:
                logger.warning("OCR file not found, using empty dict")
                ocr = {}

            # Get audio transcripts
            audio_transcripts = []
            try:
                audio = self.read_file_from_s3(f'input_folder/{video_id}/{video_id}.srt')
                audio_transcripts = self.parse_srt(audio)
            except Exception as e:
                logger.info(f"Using alternative transcript file format due to: {str(e)}")
                try:
                    audio = self.read_file_from_s3(f'input_folder/{video_id}/{video_id}.mp4.whisper.turbo.en.srt')
                    audio_transcripts = self.parse_srt(audio)
                except Exception as e2:
                    logger.warning(f"Could not read transcript file: {str(e2)}")

            # Determine which shots to process
            if shot_index is not None:
                shots_to_process = [shots[int(shot_index)]]
            else:
                shots_to_process = shots

            logger.info(f"Processing {len(shots_to_process)} shots for video {video_id}")

            for shot_metadata in shots_to_process:
                shot = {}
                shot["video_id"] = video_id
                shot["fps"] = metadata['streams'][0]['r_frame_rate']
                shot["shot_loader"] = {}
                shot["shot_loader"]["shot_index"] = shot_metadata["shot_id"]
                shot["shot_loader"]["shot_description"] = shot_metadata["content"]
                shot["shot_loader"]["frame_index"] = [int(shot_metadata['start_frame']), int(shot_metadata['end_frame'])]

                # Find which scenes overlap with this shot
                shot_scenes = [
                    scene for scene in scenes
                    if (
                        shot["shot_loader"]["frame_index"][0] >= scene['start_frame'] and
                        shot["shot_loader"]["frame_index"][0] <= scene['end_frame']
                    ) or (
                        shot["shot_loader"]["frame_index"][1] >= scene['start_frame'] and
                        shot["shot_loader"]["frame_index"][1] <= scene['end_frame']
                    )
                ]

                if shot_scenes:
                    # Prefer the scene containing the shot's start frame (majority of shot);
                    # fall back to last matching scene if none contains the start frame
                    shot_start = shot["shot_loader"]["frame_index"][0]
                    start_scenes = [s for s in shot_scenes if s['start_frame'] <= shot_start <= s['end_frame']]
                    best_scene = start_scenes[0] if start_scenes else shot_scenes[-1]
                    shot["shot_loader"]["scene_index"] = best_scene['scene_id']
                    shot["shot_loader"]["scene_description"] = best_scene['content']
                    shot["shot_loader"]["scene_frame_index"] = [int(best_scene['start_frame']), int(best_scene['end_frame'])]

                    # Clip shot boundaries to scene boundaries
                    original_start = shot["shot_loader"]["frame_index"][0]
                    original_end = shot["shot_loader"]["frame_index"][1]
                    scene_start = shot["shot_loader"]["scene_frame_index"][0]
                    scene_end = shot["shot_loader"]["scene_frame_index"][1]

                    # Clip both start and end to stay within scene boundaries
                    clipped_start = max(original_start, scene_start)
                    clipped_end = min(original_end, scene_end)

                    # Update with clipped values
                    shot["shot_loader"]["frame_index"] = [clipped_start, clipped_end]

                else:
                    shot["shot_loader"]["scene_index"] = -1
                    shot["shot_loader"]["scene_description"] = ""
                    shot["shot_loader"]["scene_frame_index"] = [-1, -1]

                shot_frames = [
                    frame
                    for frame in frames
                    if (
                        frame['frameIndex'] >= shot["shot_loader"]["frame_index"][0] and
                        frame['frameIndex'] <= shot["shot_loader"]["frame_index"][1]
                    )
                ]

                ## image_metadata_loader and frame_selection_loader
                shot['image_metadata_loader'] = {}
                shot['image_metadata_loader']['image_description'] = []
                shot['image_metadata_loader']['ocr'] = []
                shot['frame_selection_loader'] = {}
                shot['frame_selection_loader']["frame"] = []
                shot['frame_selection_loader']["frame_idx"] = []

                # pHash selection already done by frame_selector, so use all frames from BDA
                for frame in shot_frames:
                    file_key = f'{video_id}/phash_selected_frames/frame_{frame["frameIndex"]:04d}.jpg'
                    image_bytes = self.read_image_bytes_from_s3(file_key)

                    if image_bytes is not None:
                        base64_encoded = base64.b64encode(image_bytes).decode('utf-8')
                        shot['frame_selection_loader']["frame"].append(base64_encoded)
                        shot['frame_selection_loader']["frame_idx"].append(frame["frameIndex"])
                        shot['image_metadata_loader']['image_description'].append(frame['content'])
                        try:
                            shot['image_metadata_loader']['ocr'].append(", ".join([el[0] for el in ocr[str(frame["frameIndex"])]]))
                        except Exception as e:
                            logger.debug(f"OCR data absent for frame {frame['frameIndex']}: {e}")
                            shot['image_metadata_loader']['ocr'].append("")

                logger.info(f"Shot {shot_metadata['shot_id']}: using {len(shot['frame_selection_loader']['frame_idx'])}/{len(shot_frames)} frames (pre-selected by pHash)")

                ## audio_transcription_loader
                shot['audio_transcription_loader'] = {}
                shot['audio_transcription_loader']['audio_transcript'] = []
                shot['audio_transcription_loader']['frame_idx'] = []

                for transcript in audio_transcripts:
                    start_frame = self.time_to_frames(transcript['start'], shot["fps"])
                    end_frame = self.time_to_frames(transcript['end'], shot["fps"])
                    shot_start = shot["shot_loader"]["frame_index"][0]
                    shot_end = shot["shot_loader"]["frame_index"][1]

                    # Use overlap: segment overlaps with shot if start <= shot_end AND end >= shot_start
                    if start_frame <= shot_end and end_frame >= shot_start:
                        # Clamp to shot range for the frame_idx record
                        clamp_start = max(start_frame, shot_start)
                        clamp_end = min(end_frame, shot_end)
                        shot['audio_transcription_loader']['frame_idx'].append([clamp_start, clamp_end])
                        shot['audio_transcription_loader']['audio_transcript'].append(transcript['text'])

                ## write metadata
                file_path = f'metadata/{video_id}/{str(shot["shot_loader"]["shot_index"])}_metadata.json'
                self.write_json_to_s3(shot, file_path)
                logger.info(f"Successfully processed shot {shot['shot_loader']['shot_index']} for video {video_id}")

            logger.info(f"Completed all {len(shots_to_process)} shots for video {video_id}")

            # Update DynamoDB status table after all metadata files are written
            status_table_name = os.environ.get('STATUS_TABLE_NAME')
            if status_table_name:
                try:
                    import time
                    dynamodb = boto3.resource('dynamodb')
                    table = dynamodb.Table(status_table_name)
                    table.put_item(
                        Item={
                            'video_id': video_id,
                            'status': 'COMPLETED',
                            'bucket_name': self.bucket_name,
                            'timestamp': int(time.time())
                        }
                    )
                    logger.info(f"Updated status table for video {video_id}")
                except Exception as e:
                    logger.error(f"Failed to update status table: {str(e)}")

            return True
            
        except Exception as e:
            logger.error(f"Error processing metadata: {str(e)}", exc_info=True)
            return False

    def preprocess_analyzer_output(self, video_id):
        """Parse analyzer outputs and prepare data for metadata processing"""
        try:
            # Define paths to the analyzer output files using video_id as the prefix
            video_analyzer_path = f"{video_id}/video_analyzer_result.json"
            image_analyzer_path = f"{video_id}/image_analyzer_result.json"
            
            logger.info(f"Reading analyzer outputs for video: {video_id}")
            
            # Read analyzer outputs - these are required files
            video_analyzer_data = self.read_json_from_s3(video_analyzer_path)
            if not video_analyzer_data:
                raise ValueError(f"Required file not found: {video_analyzer_path}")
                
            logger.info(f"Successfully read video analyzer data from {video_analyzer_path}")
            
            image_analyzer_data = self.read_json_from_s3(image_analyzer_path)
            if not image_analyzer_data:
                raise ValueError(f"Required file not found: {image_analyzer_path}")
                
            logger.info(f"Successfully read image analyzer data from {image_analyzer_path}")
            
            # Extract data from the appropriate sources
            metadata = self.extract_metadata(video_analyzer_data)
            shots = self.extract_shots(video_analyzer_data)
            frames = self.extract_frames(image_analyzer_data)
            ocr = self.extract_ocr(image_analyzer_data)
            transcript = self.extract_transcript(video_analyzer_data)
            
            # Write to expected paths
            metadata_path = f'input_folder/{video_id}/{video_id}.mp4.metadata.json'
            shots_path = f'input_folder/{video_id}/{video_id}.mp4.shots.json'
            frames_path = f'input_folder/{video_id}/{video_id}.mp4.frames.json'
            ocr_path = f'input_folder/{video_id}/{video_id}.merged_ocr_results.json'
            transcript_path = f'input_folder/{video_id}/{video_id}.srt'
            
            scenes = self.extract_scenes(video_analyzer_data)
            scenes_path = f'input_folder/{video_id}/{video_id}.mp4.scenes.json'

            # Write extracted data to expected locations
            self.write_json_to_s3(metadata, metadata_path)
            self.write_json_to_s3(shots, shots_path)
            self.write_json_to_s3(scenes, scenes_path)
            self.write_json_to_s3(frames, frames_path)
            self.write_json_to_s3(ocr, ocr_path)
            
            # Write transcript as text file
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=transcript_path,
                Body=transcript,
                ContentType='text/plain'
            )
            
            logger.info(f"Successfully preprocessed analyzer outputs for {video_id}")
            return True
        except Exception as e:
            logger.error(f"Error preprocessing analyzer output: {str(e)}", exc_info=True)
            return False

    def extract_metadata(self, analyzer_data):
        """Extract metadata from analyzer output"""
        try:
            # Create streams structure expected by downstream processing
            # BDA nests video properties under 'metadata' key
            bda_meta = analyzer_data.get("metadata", {})
            metadata = {
                "streams": [
                    {
                        "r_frame_rate": str(bda_meta.get("frame_rate", "24/1")),
                        "width": bda_meta.get("frame_width", 0),
                        "height": bda_meta.get("frame_height", 0),
                        "codec_name": bda_meta.get("codec", "h264"),
                        "duration": str(bda_meta.get("duration_millis", 0) / 1000),
                        "format": bda_meta.get("format", "")
                    }
                ],
                "format": {
                    "duration": str(bda_meta.get("duration_millis", 0) / 1000),
                    "filename": bda_meta.get("s3_key", ""),
                    "format_name": bda_meta.get("format", "")
                }
            }
            return metadata
        except Exception as e:
            logger.error(f"Error extracting metadata: {str(e)}")
            return {"streams": [{"r_frame_rate": "24/1"}]}

    def extract_shots(self, analyzer_data):
        """Extract shots data from analyzer output"""
        shots = []
        try:
            # Build chapter summary lookup keyed by shot_index
            chapter_by_shot = {}
            for chapter in analyzer_data.get("chapters", []):
                summary = chapter.get("summary", "")
                for shot_idx in chapter.get("shot_indices", []):
                    chapter_by_shot[shot_idx] = summary

            # Process each shot from the analyzer data
            if "shots" in analyzer_data:
                for i, shot_data in enumerate(analyzer_data["shots"]):
                    shot_index = shot_data.get("shot_index", i)
                    shot = {
                        "shot_id": shot_index,
                        "content": chapter_by_shot.get(shot_index, ""),
                        "start_frame": shot_data.get("start_frame_index", 0),
                        "end_frame": shot_data.get("end_frame_index", 0),
                        "start_time": shot_data.get("start_timestamp_millis", 0) / 1000,
                        "end_time": shot_data.get("end_timestamp_millis", 0) / 1000,
                        "confidence": shot_data.get("confidence", 1.0)
                    }
                    shots.append(shot)
                    
            # If no shots data is available, create a default single shot
            if not shots:
                logger.warning("No shots data found, creating default shot")
                shots.append({
                    "shot_id": 0,
                    "content": "",
                    "start_frame": 0,
                    "end_frame": int(analyzer_data.get("duration_millis", 0) * 
                                analyzer_data.get("frame_rate", 30) / 1000),
                    "start_time": 0,
                    "end_time": analyzer_data.get("duration_millis", 0) / 1000,
                    "confidence": 1.0
                })
                
            return shots
        except Exception as e:
            logger.error(f"Error extracting shots: {str(e)}")
            return []

    def extract_scenes(self, analyzer_data):
        """Extract scenes from BDA chapter data (chapters serve as scenes in us-east-1)"""
        scenes = []
        try:
            for i, chapter_data in enumerate(analyzer_data.get("chapters", [])):
                scene = {
                    "scene_id": i,
                    "content": chapter_data.get("summary", ""),
                    "start_frame": chapter_data.get("start_frame_index", 0),
                    "end_frame": chapter_data.get("end_frame_index", 0),
                    "start_time": chapter_data.get("start_timestamp_millis", 0) / 1000,
                    "end_time": chapter_data.get("end_timestamp_millis", 0) / 1000,
                }
                scenes.append(scene)

            if not scenes:
                logger.warning("No chapters data found in BDA output for scene extraction")

            return scenes
        except Exception as e:
            logger.error(f"Error extracting scenes: {str(e)}")
            return []

    def extract_frames(self, analyzer_data):
        """Extract frames data from analyzer output"""
        frames = []
        try:
            # BDA image analysis result: list of per-frame dicts with 'image' or 'document' and 'source_image'
            if isinstance(analyzer_data, list):
                for item in analyzer_data:
                    if not isinstance(item, dict):
                        continue
                    source_image = item.get('source_image') or item.get('metadata', {}).get('s3_key', '')
                    if not source_image:
                        continue
                    frame_index_match = re.search(r'frame_(\d+)\.jpg', source_image)
                    if not frame_index_match:
                        continue
                    frame_index = int(frame_index_match.group(1))
                    if 'image' in item:
                        content = item['image'].get('summary', '')
                    elif 'document' in item:
                        # Presentation slides classified as document: use page markdown as description
                        pages = item.get('pages', [])
                        content = pages[0].get('representation', {}).get('markdown', '') if pages else ''
                    else:
                        continue
                    frames.append({
                        "frameIndex": frame_index,
                        "content": content,
                        "source_image": source_image
                    })

            # Sort frames by index for consistency
            frames.sort(key=lambda x: x["frameIndex"])

            return frames
        except Exception as e:
            logger.error(f"Error extracting frames: {str(e)}")
            return []

    def extract_transcript(self, analyzer_data):
        """Extract and format transcript as SRT from BDA video analyzer output"""
        try:
            srt = ""
            # BDA video output structure: analyzer_data is a dict with 'video.transcript'
            # and per-shot audio segments under 'shots[].audio_segments'
            if not isinstance(analyzer_data, dict):
                return srt

            # Try per-shot audio segments first (timecoded)
            segments = []
            for shot in analyzer_data.get("shots", []):
                segments.extend(shot.get("audio_segments", []))

            if segments:
                for i, segment in enumerate(segments):
                    start_time = self.format_srt_time(segment.get('start_timestamp_millis', 0) / 1000)
                    end_time = self.format_srt_time(segment.get('end_timestamp_millis', 0) / 1000)
                    text = segment.get('text', '').strip()
                    if text:
                        srt += f"{i+1}\n{start_time} --> {end_time}\n{text}\n\n"
            else:
                # Fall back to full video transcript text
                full_text = (analyzer_data.get("video", {})
                             .get("transcript", {})
                             .get("representation", {})
                             .get("text", "")).strip()
                if full_text:
                    duration_ms = analyzer_data.get("metadata", {}).get("duration_millis", 600000)
                    end_time = self.format_srt_time(duration_ms / 1000)
                    srt = f"1\n00:00:00,000 --> {end_time}\n{full_text}\n\n"

            return srt
        except Exception as e:
            logger.error(f"Error extracting transcript: {str(e)}")
            return "1\n00:00:00,000 --> 00:00:10,000\nNo transcript available.\n\n"

    def extract_ocr(self, analyzer_data):
        """Extract OCR data from analyzer output"""
        ocr_data = {}
        try:
            # BDA image analysis result: list of per-frame dicts with 'image.text_lines' or 'document.elements'
            if isinstance(analyzer_data, list):
                for item in analyzer_data:
                    if not isinstance(item, dict):
                        continue
                    if 'image' not in item and 'document' not in item:
                        continue
                    source_image = item.get('source_image') or item.get('metadata', {}).get('s3_key', '')
                    if not source_image:
                        continue
                    frame_index_match = re.search(r'frame_(\d+)\.jpg', source_image)
                    if frame_index_match:
                        frame_idx = str(int(frame_index_match.group(1)))  # normalize: "0042" -> "42"
                        if 'image' in item:
                            text_lines = item['image'].get('text_lines', [])
                            if text_lines:
                                ocr_data[frame_idx] = [
                                    [line['text'], line.get('confidence', 0.99)]
                                    for line in text_lines
                                    if line.get('text')
                                ]
                        elif 'document' in item:
                            # Document type: extract text from elements
                            elements = item.get('elements', [])
                            texts = [
                                el.get('representation', {}).get('markdown', '')
                                for el in elements
                                if el.get('type') == 'TEXT' and el.get('representation', {}).get('markdown')
                            ]
                            if texts:
                                ocr_data[frame_idx] = [[text, 0.99] for text in texts]

            return ocr_data
        except Exception as e:
            logger.error(f"Error extracting OCR: {str(e)}")
            return {}

    def format_srt_time(self, seconds):
        """Format time in seconds to SRT time format (HH:MM:SS,mmm)"""
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        secs = int(seconds % 60)
        milliseconds = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

    def list_files_in_s3(self, prefix=''):
        """
        List all files in an S3 bucket, handling pagination for more than 1000 files
        """
        files = []

        # Initialize the paginator
        paginator = self.s3_client.get_paginator('list_objects_v2')
        
        # Create a PageIterator from the paginator
        page_iterator = paginator.paginate(
            Bucket=self.bucket_name,
            Prefix=prefix
        )

        try:
            # Iterate through each page
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        files.append(obj['Key'])
            return files

        except Exception as e:
            logger.error(f"Error listing files: {str(e)}")
            return None

    def read_json_from_s3(self, file_key):
        """
        Read a JSON file from S3 and return its contents as a Python dictionary
        """
        try:
            # Get the object from S3
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=file_key
            )
            
            # Read the JSON content
            json_content = response['Body'].read().decode('utf-8')
            
            # Parse JSON into a Python dictionary
            data = json.loads(json_content)
            
            return data
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.warning(f"File not found in S3: {file_key}")
                return None
            else:
                logger.error(f"Error reading JSON from S3 ({file_key}): {str(e)}")
                return None
        except Exception as e:
            logger.error(f"Error reading JSON from S3 ({file_key}): {str(e)}")
            return None

    def read_file_from_s3(self, file_key):
        """
        Read a text file from S3
        """
        try:
            # Get the object from S3
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=file_key)
            
            # Read the content of the file
            file_content = response['Body'].read().decode('utf-8')
            
            return file_content
        
        except Exception as e:
            logger.error(f"Error reading file from S3 ({file_key}): {str(e)}")
            raise

    def read_image_bytes_from_s3(self, file_key):
        """Read an image file from S3 and return raw bytes"""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=file_key)
            return response['Body'].read()
        except Exception as e:
            logger.warning(f"Error reading image from S3 ({file_key}): {str(e)}")
            return None

    def read_png_from_s3(self, file_key):
        """
        Read a PNG file from S3 and encode it as base64
        """
        try:
            # Get the object from S3
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=file_key)
            
            # Read the content of the file
            image_content = response['Body'].read()
            
            # Encode the image content as base64
            base64_encoded = base64.b64encode(image_content).decode('utf-8')
            
            return base64_encoded
        
        except Exception as e:
            logger.warning(f"Error reading PNG file from S3 ({file_key}): {str(e)}")
            return None

    def write_json_to_s3(self, data, file_path):
        """
        Write JSON data to an S3 bucket
        """
        try:
            # Convert data to JSON string
            json_data = json.dumps(data)
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.output_bucket,
                Key=file_path,
                Body=json_data,
                ContentType='application/json'
            )
            
            logger.info(f"Successfully wrote JSON to s3://{self.output_bucket}/{file_path}")
            
        except Exception as e:
            logger.error(f"Error writing to S3: {str(e)}")
            raise

    def parse_srt(self, content):
        """
        Parse SRT subtitle content
        """
        # Remove BOM if present
        content = content.lstrip('\ufeff')
        
        # Split the content into subtitle blocks
        subtitle_blocks = re.split(r'\n\n+', content.strip())
        
        subtitles = []
        for block in subtitle_blocks:
            lines = block.split('\n')
            if len(lines) >= 3:  # Ensure we have at least index, time, and text
                try:
                    index = int(re.search(r'\d+', lines[0]).group())
                    time_range = lines[1]
                    text = ' '.join(lines[2:])
                    
                    # Parse the time range
                    start, end = time_range.split(' --> ')
                    
                    subtitles.append({
                        'index': index,
                        'start': start.strip(),
                        'end': end.strip(),
                        'text': text
                    })
                except (AttributeError, ValueError):
                    # Skip this block if we can't parse the index
                    continue
        
        return subtitles

    def time_to_frames(self, time_str, fps):
        """
        Convert time string to frame number
        """
        # Convert frame rate calculation (safely parse "24000/1001" style fractions)
        fps_str = str(fps)
        if '/' in fps_str:
            numerator, denominator = fps_str.split('/', 1)
            fps = float(numerator) / float(denominator)
        else:
            fps = float(fps_str)
        
        # Parse the time string (HH:MM:SS,mmm)
        hours, minutes, seconds = time_str.split(':')
        seconds, milliseconds = seconds.split(',')
        
        # Convert to total seconds
        total_seconds = (int(hours) * 3600 + 
                        int(minutes) * 60 + 
                        int(seconds) + 
                        int(milliseconds) / 1000)
        
        # Convert to frame number
        frame_number = round(total_seconds * fps)
        
        return frame_number

def main():
    """
    Main entry point for the application
    """
    try:
        if len(sys.argv) >= 3:
            # Command line arguments
            video_id = sys.argv[1]
            shot_index = sys.argv[2]
        else:
            # Environment variables (for ECS Task)
            video_id = os.environ.get('VIDEO_ID')
            shot_index = os.environ.get('SHOT_INDEX')  # None means process all shots
            
            # Check if we have the required parameters
            if not video_id:
                raise ValueError("Missing required parameter: VIDEO_ID")
        
        logger.info(f"Starting metadata processing for video {video_id}, shot {shot_index}")
        
        processor = MetadataProcessor()
        success = processor.process_metadata(video_id, shot_index)
        
        if success:
            logger.info(f"Metadata processing completed successfully")
            sys.exit(0)
        else:
            logger.error("Metadata processing failed")
            sys.exit(1)
    
    except Exception as e:
        logger.error(f"Error in main function: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()