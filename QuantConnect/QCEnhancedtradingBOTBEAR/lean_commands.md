# QuantConnect lean commands

# lean Login

/Users/mayankmehrotra/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/.venv/bin/lean login --user-id 340952 --api-token 06dd6fb925a65e3b7e20d5365a4faf74fe21c43912b5c809d5ef07cf87baf4bd

cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect


# Quick Reference for Your Workflow

# Activate venv first
source ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/.venv/bin/activate
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect

# Compile + backtest in one command
lean cloud backtest "QCEnhancedtradingBOTBEAR" --push --open

# Check all past backtests
lean cloud backtest list "QCEnhancedtradingBOTBEAR"

# Push code only (compile check)
lean cloud push --project "QCEnhancedtradingBOTBEAR"

# General

# Show all commands
lean --help

# Show help for a specific command
lean cloud backtest --help

# Show Lean CLI version
lean --version

# Docker (Local Engine)

# Pull latest Lean Docker image
lean docker pull

# Check Docker status
lean docker status

# Authentication

# Login to QuantConnect
lean login

# Logout
lean logout

# Check current login status
lean whoami

# Project Management

# Create a new project
lean create-project "MyProject" --language python

# Push local project to cloud
lean cloud push --project "QCEnhancedtradingBOTBEAR"

# Pull cloud project to local
lean cloud pull --project "QCEnhancedtradingBOTBEAR"

# List all cloud projects
lean cloud ls

# Delete a cloud project
lean cloud delete --project "QCEnhancedtradingBOTBEAR"

# Backtesting

# Run cloud backtest
lean cloud backtest "QCEnhancedtradingBOTBEAR" --push --open

# Run cloud backtest with custom name
lean cloud backtest "QCEnhancedtradingBOTBEAR" --name "My Bear Test" --push --open

# Run local backtest (requires Docker)
lean backtest "QCEnhancedtradingBOTBEAR"

# Run local backtest with specific config
lean backtest "QCEnhancedtradingBOTBEAR" --output ./results

# Backtest Results

# List all backtests for a project
lean cloud backtest list "QCEnhancedtradingBOTBEAR"

# Read/view a specific backtest report
lean cloud backtest report "QCEnhancedtradingBOTBEAR" --backtest-id "BACKTEST_ID" --open

# Download backtest results
lean cloud backtest report "QCEnhancedtradingBOTBEAR" --backtest-id "BACKTEST_ID" --output ./report.html

# Live Trading

# Deploy live to cloud
lean cloud live "QCEnhancedtradingBOTBEAR" --push

# Deploy live locally (requires Docker + brokerage config)
lean live "QCEnhancedtradingBOTBEAR"

# Stop a live deployment
lean cloud live stop "QCEnhancedtradingBOTBEAR"

# Liquidate all positions and stop
lean cloud live liquidate "QCEnhancedtradingBOTBEAR"

# List live deployments
lean cloud live list

# Optimization

# Run cloud optimization
lean cloud optimize "QCEnhancedtradingBOTBEAR" --push

# Run local optimization (requires Docker)
lean optimize "QCEnhancedtradingBOTBEAR"

# Research

# Open a cloud research notebook
lean cloud research "QCEnhancedtradingBOTBEAR"

# Open local research notebook (requires Docker)
lean research "QCEnhancedtradingBOTBEAR"

# Data
# Download data for local use
lean data download --dataset "US Equities" --ticker "AAPL"

# List available datasets
lean data ls

# Generate data for local backtesting
lean data generate --start 20200101 --end 20241231 --symbol-count 10 --resolution Hour

# Configuration & Setup

# Initialize Lean in current directory
lean init

# Show current config
lean config list

# Set a config value
lean config set "job-organization-id" "YOUR_ORG_ID"

# Get a config value
lean config get "job-organization-id"

# Libraries & Dependencies

# Add a Python library to project
lean library add "QCEnhancedtradingBOTBEAR" pandas

# Remove a library
lean library remove "QCEnhancedtradingBOTBEAR" pandas


