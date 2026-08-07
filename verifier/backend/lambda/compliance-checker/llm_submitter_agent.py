from typing import List, Any, Dict
import json
import time
import base64

import boto3

from strands import Agent
from strands.models import BedrockModel


class LLMSubmitter:
    """Handles submissions to the LLM (agent) service"""
    
    def __init__(self, 
                 model_id: str,
                 region_name: str = "us-east-1",
                 model_version: str = "bedrock-2024-12-03",
                 max_tokens: int = 32000, # max 10k for nova pro
                 temperature: float = 0,
                 top_p: float = 0.2,
                 stop_sequences: List[str] = None,
                 retry_attempts: int = 3,
                 retry_delay: float = 1.0):
        """
        Initialize LLM submitter with configurable parameters.
        
        Args:
            model_id: The ID of the model to use
            region_name: AWS region name
            model_version: Version of the model API
            max_tokens: Maximum number of tokens in the response
            temperature: Controls randomness in the output (0.0-1.0)
            top_p: Nucleus sampling parameter (0.0-1.0)
            stop_sequences: List of sequences where the model should stop generating
            retry_attempts: Number of retry attempts for failed requests
            retry_delay: Delay between retry attempts in seconds
        """
        self.model_id = model_id
        self.region_name = region_name
        self.model_version = model_version
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.stop_sequences = stop_sequences or []
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        
        self.agent = self._initialize_agent()

    def _initialize_agent(self) -> Any:
        """Initialize the AWS client with configured parameters."""
        try:
            # Specify the Bedrock ModelID
            bedrock_model = BedrockModel(
                model_id=self.model_id,
                region_name=self.region_name,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                top_p=self.top_p,
                stop_sequences=self.stop_sequences
            )
            # Create the agent with specialized tools
            return Agent(
                model=bedrock_model,
                system_prompt=None,
                callback_handler=None,
                tools=[]
            )            
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Strands agent: {e}")

    def submit(self, 
               content: List[Dict[str, Any]]
               ) -> str:
        """
        Submit content to agent for analysis.
        
        Args:
            content: The content to submit to the agent            
            
        Returns:
            Agent response (string)
            
        Raises:
            RuntimeError: If the agent request fails after all retry attempts
        """
        request = self._build_request(content)

        for attempt in range(self.retry_attempts):
            try:
                response = self.agent(
                    content
                ) # strand agent multi-modal example: https://strandsagents.com/latest/documentation/docs/user-guide/concepts/agents/prompts/
                return str(response)

            except Exception as e:
                if attempt == self.retry_attempts - 1:
                    raise RuntimeError(f"Error invoking agent after {self.retry_attempts} attempts: {e}")
                time.sleep(self.retry_delay * (attempt + 1))  # Exponential backoff

    def _build_request(self,
                      content: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Build the request object with default parameters.

        Args:
            content: The content to submit

        Returns:
            Dictionary containing the complete request
        """
        request = []
        for item in content:
            if 'text' in item:
                request.append(item)
            elif 'image' in item:
                # Convert the base64 string back to bytes
                image_base64 = item['image']['source']['bytes']
                image_bytes = base64.b64decode(image_base64)
                item['image']['source']['bytes'] = image_bytes
                request.append(item)

        return request