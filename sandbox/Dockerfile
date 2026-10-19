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

# Set PYTHONPATH so tests can import from /workspace regardless of test runner invocation
ENV PYTHONPATH=/workspace

# Install common test runners and tooling. Repository dependencies are NOT baked
# in here: the test runner builds a cached per-repository image from each repo's
# requirements files / pyproject.toml, so a repo that forgets to declare a
# dependency fails visibly instead of passing by accident.
RUN pip install --no-cache-dir \
    pytest \
    pytest-cov \
    flake8 \
    black

# The container will be overridden by the agent executing specific code
CMD ["/bin/bash"]
