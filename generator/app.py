#Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#!/usr/bin/env python3
import os
from aws_cdk import App, Environment
from vcp_2a.fargate_ecs import VCPFargateStack

VCP2aApp = App()
env = Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)
VCPFargateStack(VCP2aApp, "VCPFargateStack", env=env)
VCP2aApp.synth()