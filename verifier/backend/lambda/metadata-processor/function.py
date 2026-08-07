import base64
import json
import logging
import re
import os
import boto3
from timecode import Timecode
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    try:

        # Extract bucket_name and video_id from the input event
        # s3_uri = event['videoDetails']['s3Uri']
        # video_id = event['videoDetails']['videoId']
        # bucket_name = s3_uri.split('/')[2]

        bucket_name = os.environ['INPUT_BUCKET_NAME']
        video_id = event['videoId']
        shot_index = event['shot_index']
        
        logger.info(f"Processing video ID: {video_id} in bucket: {bucket_name}")
        
        # Get video metadata
        metadata = read_json_from_s3(bucket_name, f'input_folder/{video_id}/{video_id}.mp4.metadata.json')
        
        # Get shots information
        shots = read_json_from_s3(bucket_name, f'input_folder/{video_id}/{video_id}.mp4.shots.json')

        # Get scenes information
        scenes = read_json_from_s3(bucket_name, f'input_folder/{video_id}/{video_id}.mp4.scenes.json')
        
        # Get frames description
        frames = read_json_from_s3(bucket_name, f'input_folder/{video_id}/{video_id}.mp4.frames.json')
        
        # Get OCR data
        ocr = read_json_from_s3(bucket_name, f'input_folder/{video_id}/{video_id}.merged_ocr_results.json')
        
        # Get audio transcripts
        try:
            audio = read_file_from_s3(bucket_name, f'input_folder/{video_id}/{video_id}.srt')
            audio_transcripts = parse_srt(audio)
        except Exception as e:
            logger.info(f"Error reading primary SRT file, trying alternative: {e}")
            audio = read_file_from_s3(bucket_name, f'input_folder/{video_id}/{video_id}.mp4.whisper.turbo.en.srt')
            audio_transcripts = parse_srt(audio)
        
        # Process each shot and write metadata
        # for shot_metadata in shots:
        ## shot_loader

        shot_metadata = shots[shot_index]
        shot = {}
        shot["video_id"] = video_id
        shot["fps"] = metadata['streams'][0]['r_frame_rate']
        shot["shot_loader"] = {}
        shot["shot_loader"]["shot_index"] = shot_metadata["shot_id"]
        shot["shot_loader"]["shot_description"] = shot_metadata["content"]
        shot["shot_loader"]["frame_index"] = [int(shot_metadata['start_frame']), int(shot_metadata['end_frame'])]

        # Select shot scenes
        shot_scenes = [
            scene
            for scene in scenes
            if (
                shot["shot_loader"]["frame_index"][0] >= scene['start_frame'] and
                shot["shot_loader"]["frame_index"][0] <= scene['end_frame']
            ) or (
                shot["shot_loader"]["frame_index"][1] >= scene['start_frame'] and
                shot["shot_loader"]["frame_index"][1] <= scene['end_frame']
            )
        ]
        
        if shot_scenes:
            shot["shot_loader"]["scene_index"] = shot_scenes[-1]['scene_id']
            shot["shot_loader"]["scene_description"] = shot_scenes[-1]['content']
            shot["shot_loader"]["scene_frame_index"] = [int(shot_scenes[-1]['start_frame']), int(shot_scenes[-1]['end_frame'])]
        else:
            shot["shot_loader"]["scene_index"] = -1
            shot["shot_loader"]["scene_description"] = ""
            shot["shot_loader"]["scene_frame_index"] = [-1, -1]

        # Select shot frames
        shot_frames = [
            frame
            for frame in frames
            if (
                frame['frameIndex']>=shot["shot_loader"]["frame_index"][0] and
                frame['frameIndex']<=shot["shot_loader"]["frame_index"][1]
            )
        ]

        ## image_metadata_loader and frame_selection_loader
        shot['image_metadata_loader'] = {}
        shot['image_metadata_loader']['image_description'] = []
        shot['image_metadata_loader']['ocr'] = []
        shot['frame_selection_loader'] = {}
        shot['frame_selection_loader']["frame"] = []
        shot['frame_selection_loader']["frame_idx"] = []

        for frame in shot_frames:
            shot['image_metadata_loader']['image_description'].append(frame['content'])
            try:
                shot['image_metadata_loader']['ocr'].append(", ".join([el[0] for el in ocr[str(frame["frameIndex"])]]))
            except Exception as e:
                logger.info(f'ocr absent {frame["frameIndex"]}: {e}')
                shot['image_metadata_loader']['ocr'].append("")

            file_key = f'{video_id}/phash_selected_frames/frame_{frame["frameIndex"]:04d}.jpg'
            base64_encoded_image = read_png_from_s3(bucket_name, file_key)

            if base64_encoded_image is not None:
                shot['frame_selection_loader']["frame"].append(base64_encoded_image)
                shot['frame_selection_loader']["frame_idx"].append(frame["frameIndex"])

        ## audio_transcription_loader
        shot['audio_transcription_loader'] = {}
        shot['audio_transcription_loader']['audio_transcript'] = []
        shot['audio_transcription_loader']['frame_idx'] = []
        
        for transcript in audio_transcripts:
            start_frame = time_to_frames(transcript['start'], shot["fps"])
            end_frame = time_to_frames(transcript['end'], shot["fps"])

            if (
                (start_frame >= shot["shot_loader"]["frame_index"][0] and
                start_frame <= shot["shot_loader"]["frame_index"][1])
                or
                (end_frame >= shot["shot_loader"]["frame_index"][0] and
                end_frame <= shot["shot_loader"]["frame_index"][1])
            ):
                shot['audio_transcription_loader']['frame_idx'].append([start_frame, end_frame])
                shot['audio_transcription_loader']['audio_transcript'].append(transcript['text'])

        ## write metadata
        file_path = f'metadata/{video_id}/{str(shot["shot_loader"]["shot_index"])}_metadata.json'
        write_json_to_s3(shot, bucket_name, file_path)
    
        return {
            'statusCode': 200,
            'body': json.dumps(f'Successfully processed {len(shots)} shots for video {video_id}')
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error processing video: {str(e)}')
        }

def list_files_in_s3(bucket_name, prefix=''):
    """
    List all files in an S3 bucket, handling pagination for more than 1000 files
    """
    s3 = boto3.client('s3')
    files = []

    # Initialize the paginator
    paginator = s3.get_paginator('list_objects_v2')
    
    # Create a PageIterator from the paginator
    page_iterator = paginator.paginate(
        Bucket=bucket_name,
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

def read_file_from_s3(bucket_name, file_key):
    """
    Read a text file from S3
    """
    s3 = boto3.client('s3')
    
    try:
        # Get the object from S3
        response = s3.get_object(Bucket=bucket_name, Key=file_key)
        
        # Read the content of the file
        file_content = response['Body'].read().decode('utf-8')
        
        return file_content
    
    except Exception as e:
        logger.error(f"Error reading file from S3: {str(e)}")
        return None

def read_png_from_s3(bucket_name, file_key):
    """
    Read a PNG file from S3 and encode it as base64
    """
    s3 = boto3.client('s3')
    
    try:
        # Get the object from S3
        response = s3.get_object(Bucket=bucket_name, Key=file_key)
        
        # Read the content of the file
        image_content = response['Body'].read()
        
        # Encode the image content as base64
        base64_encoded = base64.b64encode(image_content).decode('utf-8')
        
        return base64_encoded
    
    except Exception as e:
        logger.error(f"Error reading PNG file from S3: {str(e)}")
        logger.error(f"The specified key does not exist: {file_key}")
        return None

def write_json_to_s3(data, bucket_name, file_path):
    """
    Write JSON data to an S3 bucket
    """
    try:
        # Initialize S3 client
        s3_client = boto3.client('s3')
        
        # Convert data to JSON string
        json_data = json.dumps(data)
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=file_path,
            Body=json_data,
            ContentType='application/json'
        )
        
        logger.info(f"Successfully wrote JSON to s3://{bucket_name}/{file_path}")
        
    except Exception as e:
        logger.error(f"Error writing to S3: {str(e)}")

def parse_srt(content):
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

def time_to_frames(time_str, fps):    
    return Timecode(fps, time_str.replace(',', '.')).frames