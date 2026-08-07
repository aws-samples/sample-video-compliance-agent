from timecode import Timecode
from typing import Dict, Any, List

from content_builder_nova import ContentBuilder
from response_parser_nova import Assessment
from llm_submitter_agent import LLMSubmitter
from response_parser_nova import ResponseParser
import json
import boto3
from urllib.parse import urlparse
import logging
import os
import math
import copy

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def s3_file_reader(s3_path: str, region: str = "us-east-1") -> dict:
    """
    Read a file from an S3 path and return it.

    Args:
        s3_path (str): The S3 path to the file (e.g., 's3://bucket-name/path/to/metadata.json')

    Returns:
        dict: The file content

    Raises:
        ValueError: If the S3 path is invalid
        Exception: If there's an error reading from S3 or parsing the JSON
    """
    # Parse the S3 URL to extract bucket and key
    try:
        parsed_url = urlparse(s3_path)
        if parsed_url.scheme != "s3":
            raise ValueError(
                f"Invalid S3 URL scheme: {parsed_url.scheme}. Expected 's3'"
            )

        bucket_name = parsed_url.netloc
        object_key = parsed_url.path.lstrip("/")

        if not bucket_name or not object_key:
            raise ValueError(
                f"Invalid S3 path format: {s3_path}. Expected format: 's3://bucket-name/path/to/file'"
            )
    except Exception as e:
        raise ValueError(f"Error parsing S3 path {s3_path}: {str(e)}")

    # Initialize S3 client
    try:
        s3_client = boto3.client("s3", region_name=region)

        # Get the object from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=object_key)

        # Read the content and parse as JSON
        content = response["Body"].read().decode("utf-8")

        return content
    except Exception as e:
        raise Exception(f"Error reading metadata from S3 path {s3_path}: {str(e)}")


def s3_report_writer(result, s3_output_path, region="us-east-1"):
    # Parse the S3 URL to extract bucket and key
    parsed_url = urlparse(s3_output_path)
    if parsed_url.scheme != "s3":
        raise ValueError(f"Invalid S3 URL scheme: {parsed_url.scheme}. Expected 's3'")

    bucket_name = parsed_url.netloc
    object_key = parsed_url.path.lstrip("/")

    if not bucket_name or not object_key:
        raise ValueError(
            f"Invalid S3 path format: {s3_output_path}. Expected format: 's3://bucket-name/path/to/file'"
        )

    # Initialize S3 client
    s3_client = boto3.client("s3", region_name=region)

    nice_output = []
    for assessment in result:
        output = {
            "frame_start_index": assessment.frame_start_index,
            "frame_end_index": assessment.frame_end_index,
            "issue": assessment.issue,
            "code": assessment.code,
            "explanation": assessment.explanation,
            "extended_explanation": assessment.extended_explanation,
            #"recommendations": assessment.recommendations,
            "confidence_score": assessment.confidence_score.value,
            "tier": assessment.tier.value
        }
        nice_output.append(output)

    # Serialize the result to JSON
    json_data = json.dumps(nice_output)

    # Write the JSON data to S3
    try:
        s3_client.put_object(Bucket=bucket_name, Key=object_key, Body=json_data)
    except Exception as e:
        raise Exception(f"Error writing to S3 path {s3_output_path}: {str(e)}")
    return


class ComplianceChecker:
    """Main class to orchestrate the compliance review process"""

    def __init__(
        self,
        metadata=f"metadata.json",
        prompt_path=f"ofcom_broadcast_guidelines_v4.txt",
        model_id=os.environ.get("MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
        num_images: int = 30,
    ):
        self.metadata = metadata
        self.prompt_path = prompt_path
        self.model_id = model_id
        self.num_images = num_images

        self._content_builder = ContentBuilder(num_images)

        self.results: List[Assessment] = []
        self.report: Dict[str, Any] = {}

        # self.metadata = self._load_metadata_from_s3()
        self.prompt = self._load_prompt_from_s3()

    def _load_prompt(self) -> str:
        """Load compliance review prompt from file."""
        try:
            with open(self.prompt_path, "r") as f:
                return f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"Prompt file not found at {self.prompt_path}")

    def _load_prompt_from_s3(self) -> str:
        """Load compliance review prompt from a s3 file."""
        try:
            return s3_file_reader(self.prompt_path)
        except FileNotFoundError:
            raise FileNotFoundError(f"Prompt file not found at {self.prompt_path}")

    def _load_metadata(self) -> Dict[str, Any]:
        """Load metadata from file."""
        try:
            with open(self.metadata, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Metadata file not found at {self.metadata}")

    def _load_metadata_from_s3(self) -> Dict[str, Any]:
        """Load metadata from a s3 file."""
        try:
            content = s3_file_reader(self.metadata)
            metadata = json.loads(content)
            return metadata
        except FileNotFoundError:
            raise FileNotFoundError(f"Metadata file not found at {self.metadata}")

    def review(self) -> Dict[str, Any]:
        """Perform complete compliance review."""
        outbound_content = self._build_outbound_content_object(self.metadata)
        llm_submitter = LLMSubmitter(self.model_id)
        self.llm_response = llm_submitter.submit(outbound_content)

        response_parser = ResponseParser()
        self.results = response_parser.parse(self.llm_response)
        # self.report = self._build_report()
        # return self.report
        return self.results

    def _build_outbound_content_object(self, metadata) -> List[Dict[str, Any]]:
        """Build content object for LLM submission."""
        return self._content_builder.build(metadata, self.prompt)

    def _build_report(self) -> Dict[str, Any]:
        """Build final compliance report."""
        pass

    def _determine_global_compliance(self) -> str:
        """Determine overall compliance status."""
        pass


def checkerpershot(bucket_name, video_id, shot_index):
    """
    Compliance checker per shot.
    Args:
        bucket_name (str): The S3 bucket name
        video_id (str): Video name
        shot_index (int): Shot index
    """

    # 1. Extract shot based on the video_id, shot_index, and bucket_name
    # {bucket_name}/metadata/{video_id}/metadata.json
    s3_metadata_path = os.path.join(bucket_name, "metadata/", video_id, str(shot_index)+"_metadata.json")
    content = s3_file_reader(s3_metadata_path)
    shot_metadata = json.loads(content)
    scene_index = shot_metadata["shot_loader"]["scene_index"]

    # 2. Run compliance checker per shot
    s3_prompt_path = os.path.join(
        bucket_name, "prompt/", "ofcom_broadcast_guidelines_v4.txt"
    )
    r = ComplianceChecker(
        metadata=shot_metadata, prompt_path=s3_prompt_path, num_images=30
    )
    result = r.review()

    # 3. Write per shot report to the following s3 path
    # {bucket_name}/shot_report/{video_id}/{shot_index}_report.json
    s3_output_path = os.path.join(
        bucket_name, "shot_report/", video_id, str(scene_index) + "_" + str(shot_index) + "_report.json"
    )
    s3_report_writer(result, s3_output_path)

    return

def lambda_handler(event, context):
    # """Testing Event."""
    #event = {
    #            "bucket_name": "s3://vcp2-test-bucket/",
    #            "video_id": "GMO_00000000108096_01",
    #            "shot_index": 1006
    #}
    #s3://vcp-2-backendstack-databuckete3889a50-cqgrpz45pfav/metadata/GMO_00000000018491_01/0_metadata.json

    # """AWS Lambda handler."""
    logger.info(f"Lambda handler invoked with event: {json.dumps(event)}")
    logger.info(f"Function name: {context.function_name}, remaining time: {context.get_remaining_time_in_millis()}ms")
    video_bucket = os.environ['BUCKET_NAME']
    video_bucket_path = "s3://"+video_bucket+ "/"
    video_id = event.get('videoId')
    shot_index = event['s3Uri']

    try:
        logging.info("Generate compliance report per shot......")
        checkerpershot(video_bucket_path, video_id, shot_index)
        logging.info("Done writing results!")
        return f"Success checking compliance for {video_id} in shot index {shot_index} in bucket {video_bucket_path}!"

    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        raise
