#!/bin/bash

# Setup development environment for the Merchant Moe (Mantle) farming bot.

set -e

echo "Setting up Merchant Moe (Mantle) farming bot development environment..."

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install package in development mode with dependencies
echo "Installing package with dependencies..."
pip install -e .

# Seed a local .env from the example if one is not present
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
fi

# Verify the package imports
echo "Verifying installation..."
python -c "import moe_mantle_bot; print('moe_mantle_bot imported successfully')"

echo ""
echo "Setup complete!"
echo ""
echo "To use the bot:"
echo "  1. Activate environment: source .venv/bin/activate"
echo "  2. Edit .env with your wallet/RPC settings"
echo "  3. Read-only snapshot:   moe-readonly --help"
echo "  4. Run a farm cycle:     moe-farm --once --json"
echo "  5. Manual operations:    moe --help"
