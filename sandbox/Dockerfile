# Sandbox base image for Python code execution and testing
FROM python:3.11-slim

# Install basic development utilities
RUN apt-get update && apt-get install -y \
    git \
    make \
    gcc \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /workspace

# Install common test runners and tooling
RUN pip install --no-cache-dir \
    pytest \
    pytest-cov \
    flake8 \
    black

# The container will be overridden by the agent executing specific code
CMD ["/bin/bash"]
