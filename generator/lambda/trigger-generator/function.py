import json
import boto3
import os
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ecs = boto3.client('ecs')

def lambda_handler(event, context):
    cluster = os.environ['CLUSTER_NAME']
    task_def = os.environ['TASK_DEFINITION']
    subnets = os.environ['SUBNETS'].split(',')
    security_groups = [os.environ['SECURITY_GROUPS']]
    bucket = os.environ['BUCKET_NAME']
    table_name = os.environ['TABLE_NAME']

    started = []
    failed = []

    for record in event['Records']:
        key = record['s3']['object']['key']

        if not key.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            continue

        logger.info(f"Starting task for: {key}")

        video_id = os.path.splitext(os.path.basename(key))[0]

        try:
            ecs.run_task(
                cluster=cluster,
                taskDefinition=task_def,
                launchType='FARGATE',
                networkConfiguration={
                    'awsvpcConfiguration': {
                        'subnets': subnets,
                        'securityGroups': security_groups,
                        'assignPublicIp': 'DISABLED'
                    }
                },
                overrides={
                    'containerOverrides': [
                        {'name': 'VideoProcessorContainer', 'environment': [
                            {'name': 'BUCKET_NAME', 'value': bucket},
                            {'name': 'VIDEO_S3_KEY', 'value': key}
                        ]},
                        {'name': 'FrameSelectorContainer', 'environment': [
                            {'name': 'BUCKET_NAME', 'value': bucket},
                            {'name': 'VIDEO_ID', 'value': video_id},
                            {'name': 'PHASH_THRESHOLD', 'value': '0.70'}
                        ]},
                        {'name': 'VideoAnalyzerContainer', 'environment': [
                            {'name': 'BUCKET_NAME', 'value': bucket},
                            {'name': 'VIDEO_S3_KEY', 'value': key}
                        ]},
                        {'name': 'ImageAnalyzerContainer', 'environment': [
                            {'name': 'BUCKET_NAME', 'value': bucket},
                            {'name': 'VIDEO_S3_KEY', 'value': key}
                        ]},
                        {'name': 'MetadataProcessorContainer', 'environment': [
                            {'name': 'INPUT_BUCKET_NAME', 'value': bucket},
                            {'name': 'VIDEO_ID', 'value': video_id},
                            {'name': 'STATUS_TABLE_NAME', 'value': table_name}
                        ]}
                    ]
                }
            )
            started.append(key)
        except Exception:
            logger.exception(f"Failed to start ECS task for {key}")
            failed.append(key)

    # If everything failed, raise so Lambda retries the batch.
    # If some succeeded, return 200 so successful videos don't get re-launched on retry.
    if failed and not started:
        raise RuntimeError(f"Failed to start any ECS tasks; failures: {failed}")

    return {
        'statusCode': 200,
        'body': json.dumps({'started': started, 'failed': failed})
    }
