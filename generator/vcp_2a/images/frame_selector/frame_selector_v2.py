#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
import json
import boto3
from PIL import Image
import imagehash
import os
import logging
import sys
import io

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

s3 = boto3.client('s3')

def apply_phash_selection(bucket_name: str, video_id: str, threshold: float = 0.85):
    """
    Apply pHash selection to already-extracted frames in selected_frames/
    Copy only distinctive frames to phash_selected_frames/

    Args:
        bucket_name: S3 bucket name
        video_id: Video ID (folder name)
        threshold: pHash similarity threshold (default 0.85)

    Returns:
        Number of frames selected
    """
    try:
        logger.info(f"Starting pHash frame selection for {video_id}")
        logger.info(f"Threshold: {threshold}")

        # List all frames in selected_frames/
        prefix = f"{video_id}/selected_frames/"
        paginator = s3.get_paginator('list_objects_v2')

        frame_keys = []
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    # Only include actual .jpg files, not metadata directories
                    if key.endswith('.jpg') and not key.endswith('/'):
                        frame_keys.append(key)

        # Sort frames by frame number
        frame_keys.sort(key=lambda x: int(x.split('frame_')[-1].split('.')[0]))
        logger.info(f"Found {len(frame_keys)} frames to process")

        if not frame_keys:
            logger.error("No frames found to process")
            return 0

        # Apply pHash selection
        previous_hash = None
        selected_count = 0

        for idx, frame_key in enumerate(frame_keys):
            try:
                # Download frame
                response = s3.get_object(Bucket=bucket_name, Key=frame_key)
                image_bytes = response['Body'].read()

                # Compute pHash
                pil_image = Image.open(io.BytesIO(image_bytes))
                current_hash = imagehash.average_hash(pil_image)

                # Check if frame is significant
                is_significant = False
                if previous_hash is None:
                    is_significant = True
                else:
                    hash_str1 = str(current_hash)
                    hash_str2 = str(previous_hash)
                    hash_diff = sum(c1 != c2 for c1, c2 in zip(hash_str1, hash_str2))
                    hash_similarity = 1 - (hash_diff / len(hash_str1))
                    if hash_similarity < threshold:
                        is_significant = True

                # If significant, copy to phash_selected_frames/
                if is_significant:
                    previous_hash = current_hash
                    frame_filename = os.path.basename(frame_key)
                    dest_key = f"{video_id}/phash_selected_frames/{frame_filename}"

                    # Copy frame
                    s3.copy_object(
                        Bucket=bucket_name,
                        CopySource={'Bucket': bucket_name, 'Key': frame_key},
                        Key=dest_key
                    )
                    selected_count += 1

                    if selected_count % 50 == 0:
                        logger.info(f"Selected {selected_count} frames so far (processed {idx+1}/{len(frame_keys)})")

            except Exception as e:
                logger.error(f"Error processing frame {frame_key}: {str(e)}")
                continue

        logger.info(f"pHash selection complete: selected {selected_count}/{len(frame_keys)} frames ({100*selected_count/len(frame_keys):.1f}%)")

        # Save selection metadata
        selection_metadata = {
            'total_frames': len(frame_keys),
            'selected_frames': selected_count,
            'threshold': threshold,
            'selection_rate': selected_count / len(frame_keys) if len(frame_keys) > 0 else 0
        }

        metadata_key = f"metadata/{video_id}/phash_selection_metadata.json"
        s3.put_object(
            Bucket=bucket_name,
            Key=metadata_key,
            Body=json.dumps(selection_metadata, indent=2),
            ContentType='application/json'
        )

        return selected_count

    except Exception as e:
        logger.error(f"Error in pHash selection: {str(e)}", exc_info=True)
        raise

def main():
    logger.info("Starting pHash frame selector")

    try:
        # Get environment variables
        bucket_name = os.environ.get('BUCKET_NAME')
        video_id = os.environ.get('VIDEO_ID')
        threshold = float(os.environ.get('PHASH_THRESHOLD', '0.85'))

        if not bucket_name or not video_id:
            logger.error("Missing required environment variables: BUCKET_NAME or VIDEO_ID")
            sys.exit(1)

        logger.info(f"Configuration: bucket={bucket_name}, video_id={video_id}, threshold={threshold}")

        # Apply pHash selection
        selected_count = apply_phash_selection(bucket_name, video_id, threshold)

        logger.info(f"Frame selection complete: {selected_count} frames selected")
        sys.exit(0)

    except Exception as e:
        logger.error(f"Error in main function: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
