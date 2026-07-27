"""Shared AWS Bedrock client factory.

Single source of truth for boto3 bedrock-runtime client construction.
Both iquest_streaming_service and compchat.reporting.narrative_agent
invoke Bedrock with different prompt/response shapes, but the client
itself is always built the same way — this is that one place.
"""
from __future__ import annotations

from typing import Any

import boto3


def get_bedrock_client(settings: Any) -> Any:
    return boto3.client(
        "bedrock-runtime",
        region_name=getattr(settings, "aws_region", "us-east-1"),
        aws_access_key_id=getattr(settings, "aws_access_key_id", None) or None,
        aws_secret_access_key=getattr(settings, "aws_secret_access_key", None) or None,
        aws_session_token=getattr(settings, "aws_session_token", None) or None,
    )
