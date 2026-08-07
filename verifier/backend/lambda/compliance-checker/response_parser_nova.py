from typing import List
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, validator
from enum import Enum
import logging
import re

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class ExtractError(Exception):
    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    
    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            value = value.lower()
            for member in cls:
                if member.value == value:
                    return member

class Assessment(BaseModel):
    frame_start_index: str
    frame_end_index: str
    issue: str
    code: str
    explanation: str
    extended_explanation: str
    #recommendations: str
    confidence_score: ConfidenceLevel
    tier: ConfidenceLevel
    '''
    @validator('recommendations')
    def validate_recommendations(cls, v):
        if not v.strip():
            raise ValueError("Recommendations cannot be empty")
        return v
    '''

class ResponseParser:
    FIELDS = [
        'frame_start_index', 'frame_end_index', 'issue', 'code',
        'explanation', 'extended_explanation', 'confidence_score', 'tier'
    ]

    @staticmethod
    def _extract_field_regex(text: str, field_name: str) -> str:
        """Extract field value using regex, tolerant of mismatched closing tags."""
        pattern = rf'<{field_name}>(.*?)</[^>]+>'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def parse(content: str) -> List[Assessment]:
        """
        Parse the XML-like content from the LLM response into Assessment objects.

        Args:
            content: String containing XML-formatted assessments

        Returns:
            List of Assessment objects
        """
        logger.info(f"Original LLM Response: {content}")
        if not content.strip():
            raise ExtractError("Empty content provided")
        try:
            # Split on <assessment> tags to find individual assessments
            assessment_blocks = re.findall(r'<assessment>(.*?)</assessment>', content, re.DOTALL)
            if not assessment_blocks:
                raise ExtractError("No assessments found in content")

            results: List[Assessment] = []
            for block in assessment_blocks:
                try:
                    field_values = {}
                    for field in ResponseParser.FIELDS:
                        value = ResponseParser._extract_field_regex(block, field)
                        if value is not None:
                            field_values[field] = value
                    results.append(Assessment(**field_values))
                except Exception as e:
                    logger.warning(f"Skipping assessment due to validation error: {e}")
                    logger.warning(f"Assessment block: {block[:500]}")
                    continue

            return results

        except Exception as e:
            if isinstance(e, ExtractError):
                raise
            raise ExtractError(f"Error parsing content: {str(e)}")