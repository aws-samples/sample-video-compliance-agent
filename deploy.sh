#!/bin/bash
set -e

echo "=========================================="
echo "Video Compliance Agent - Full Deployment"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if AWS CLI is configured
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS CLI not configured. Please run 'aws configure' first.${NC}"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-us-east-1}

# Export so the CDK CLI and all aws CLI calls below target the same region,
# regardless of the caller's configured default region.
export AWS_REGION="${REGION}"
export AWS_DEFAULT_REGION="${REGION}"

echo -e "${GREEN}Deploying to Account: ${ACCOUNT_ID}, Region: ${REGION}${NC}"

# Step 1: Deploy Generator Stack
echo -e "\n${YELLOW}Step 1: Deploying Generator Stack...${NC}"
cd generator

if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt

echo "Deploying VCPFargateStack..."
cdk deploy VCPFargateStack --require-approval never

# Capture outputs
echo "Capturing Generator stack outputs..."
STATUS_TABLE_NAME=$(aws cloudformation describe-stacks \
    --stack-name VCPFargateStack \
    --query "Stacks[0].Outputs[?OutputKey=='VideoStatusTableName'].OutputValue" \
    --output text)

STATUS_TABLE_STREAM_ARN=$(aws cloudformation describe-stacks \
    --stack-name VCPFargateStack \
    --query "Stacks[0].Outputs[?OutputKey=='VideoStatusTableStreamArn'].OutputValue" \
    --output text)

METADATA_BUCKET_NAME=$(aws cloudformation describe-stacks \
    --stack-name VCPFargateStack \
    --query "Stacks[0].Outputs[?OutputKey=='MetadataBucketName'].OutputValue" \
    --output text)

echo -e "${GREEN}Generator Stack Deployed Successfully!${NC}"
echo "  Status Table: ${STATUS_TABLE_NAME}"
echo "  Stream ARN: ${STATUS_TABLE_STREAM_ARN}"
echo "  Metadata Bucket: ${METADATA_BUCKET_NAME}"

# Upload the compliance guidelines prompt. The compliance checker Lambda reads this
# from s3://<metadata-bucket>/prompt/ and fails with NoSuchKey if it is missing.
echo "Uploading compliance guidelines prompt..."
aws s3 cp ../verifier/prompt/ofcom_broadcast_guidelines_v4.txt \
    "s3://${METADATA_BUCKET_NAME}/prompt/ofcom_broadcast_guidelines_v4.txt"

deactivate
cd ..

# Step 2: Deploy Verifier Stack
echo -e "\n${YELLOW}Step 2: Deploying Verifier Stack...${NC}"
cd verifier/backend

if [ ! -d "node_modules" ]; then
    echo "Installing Node.js dependencies..."
    npm install
fi

echo "Deploying VCP-2-BackendStack with Generator outputs..."
cdk deploy VCP-2-BackendStack \
    --context generatorStatusTableStreamArn="${STATUS_TABLE_STREAM_ARN}" \
    --context generatorMetadataBucket="${METADATA_BUCKET_NAME}" \
    --require-approval never

echo -e "${GREEN}Verifier Stack Deployed Successfully!${NC}"

cd ../..

# Summary
echo -e "\n${GREEN}=========================================="
echo "Deployment Complete!"
echo "==========================================${NC}"
echo ""
echo "Generator Stack:"
echo "  - Status Table: ${STATUS_TABLE_NAME}"
echo "  - Metadata Bucket: ${METADATA_BUCKET_NAME}"
echo ""
echo "Verifier Stack:"
echo "  - Automatically triggered when generator completes"
echo "  - Reads metadata from: ${METADATA_BUCKET_NAME}"
echo ""
echo "Next Steps:"
echo "  1. Upload videos to: s3://${METADATA_BUCKET_NAME}/input/"
echo "  2. ECS task will automatically start when video is uploaded"
echo "  3. Verifier will automatically start when processing completes"
echo ""
echo "Example:"
echo "  aws s3 cp your-video.mp4 s3://${METADATA_BUCKET_NAME}/input/"
echo ""
