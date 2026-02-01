#!/bin/bash
#
# Setup script for pjsua2 Python bindings
# Creates a virtual environment and builds/installs pjsua2
#
# Usage:
#   PJPROJECT_DIR=/path/to/pjproject ./scripts/setup_pjsua2.sh
#
# Environment variables:
#   PJPROJECT_DIR (required) - Path to the pjproject source directory
#                              (must be already built with ./configure && make)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Require PJPROJECT_DIR to be set
if [ -z "$PJPROJECT_DIR" ]; then
    echo "Error: PJPROJECT_DIR environment variable is not set"
    echo ""
    echo "Usage:"
    echo "  PJPROJECT_DIR=/path/to/pjproject ./scripts/setup_pjsua2.sh"
    echo ""
    echo "PJPROJECT_DIR should point to a built pjproject source directory."
    echo "See docs/PJSIP.md for build instructions."
    exit 1
fi

# Setup logging
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup_pjsua2_$(date +%Y%m%d_%H%M%S).log"

# Redirect all output to both terminal and log file
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== pjsua2 Setup Script ==="
echo "Log file: $LOG_FILE"
echo "Project directory: $PROJECT_DIR"
echo "PJSIP source directory: $PJPROJECT_DIR"
echo "Date: $(date)"
echo ""

# Check if pjproject exists
if [ ! -d "$PJPROJECT_DIR" ]; then
    echo "Error: pjproject not found at $PJPROJECT_DIR"
    echo "Set PJPROJECT_DIR environment variable to the correct path"
    exit 1
fi

# Check if pjproject was built
if [ ! -f "$PJPROJECT_DIR/pjlib/lib/libpj-"*".a" ]; then
    echo "Error: pjproject does not appear to be built"
    echo "Please build pjproject first:"
    echo "  cd $PJPROJECT_DIR"
    echo "  ./configure"
    echo "  make dep && make"
    exit 1
fi

cd "$PROJECT_DIR"

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip and install setuptools
echo "Installing/upgrading pip and setuptools..."
pip install --upgrade pip setuptools

# Build pjsua2 Python bindings
echo ""
echo "Building pjsua2 Python bindings..."
cd "$PJPROJECT_DIR/pjsip-apps/src/swig/python"

make clean
make PYTHON_EXE=python

echo ""
echo "Installing pjsua2 into virtual environment..."
pip install .

# Verify installation
echo ""
echo "Verifying pjsua2 installation..."
cd "$PROJECT_DIR"
if python -c "import pjsua2; print('pjsua2 version:', pjsua2.Endpoint().libVersion().full)"; then
    echo ""
    echo "=== Setup Complete ==="
    echo ""
    echo "pjsua2 is installed and working."
    echo ""
    echo "To use the virtual environment:"
    echo "  cd $PROJECT_DIR"
    echo "  source .venv/bin/activate"
    echo ""
    echo "To test PJSIP:"
    echo "  python -m voip_client.pjsip_test"
else
    echo ""
    echo "Error: pjsua2 verification failed"
    exit 1
fi
