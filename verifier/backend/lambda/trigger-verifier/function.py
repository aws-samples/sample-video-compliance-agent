import json
import boto3
import os
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn_client = boto3.client('stepfunctions')

def lambda_handler(event, context):
    """
    Triggered by DynamoDB Stream when generator completes processing.
    Starts the verifier Step Function for the completed video.
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    state_machine_arn = os.environ['STATE_MACHINE_ARN']
    bucket_name = os.environ['BUCKET_NAME']
    
    for record in event['Records']:
        if record['eventName'] in ['INSERT', 'MODIFY']:
            new_image = record['dynamodb'].get('NewImage', {})
            
            # Check if status is COMPLETED
            status = new_image.get('status', {}).get('S', '')
            if status != 'COMPLETED':
                logger.info(f"Skipping record with status: {status}")
                continue
            
            video_id = new_image.get('video_id', {}).get('S', '')
            if not video_id:
                logger.warning("No video_id found in record")
                continue
            
            logger.info(f"Starting verifier workflow for video: {video_id}")
            
            # Start Step Function execution for this video
            execution_input = {
                "videosList": {
                    "videos": [
                        {
                            "id": video_id,
                            "s3Uri": f"s3://{bucket_name}/input_folder/{video_id}/"
                        }
                    ]
                }
            }
            
            try:
                execution_name = f"verify-{video_id}-{int(record['dynamodb']['ApproximateCreationDateTime'])}"
                if len(execution_name) > 80:
                    execution_name = execution_name[:80]

                response = sfn_client.start_execution(
                    stateMachineArn=state_machine_arn,
                    name=execution_name,
                    input=json.dumps(execution_input)
                )
                logger.info(f"Started execution: {response['executionArn']}")
            except Exception as e:
                logger.error(f"Failed to start execution for {video_id}: {str(e)}")
                raise
    
    return {
        'statusCode': 200,
        'body': json.dumps('Successfully triggered verifier workflows')
    }
