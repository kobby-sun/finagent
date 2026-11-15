#!/usr/bin/env bash
# Build the image (linux/amd64) and push it to ECR with an immutable tag.
set -euo pipefail
. "$(dirname "$0")/_env.sh"
cd "$(dirname "$0")/../.."
REPO=$(tf_out ecr_repository_url)
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${REPO%/*}"
docker build --platform linux/amd64 -t "$REPO:$IMAGE_TAG" .
docker push "$REPO:$IMAGE_TAG"
echo "pushed $REPO:$IMAGE_TAG"
