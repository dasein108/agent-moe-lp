#!/usr/bin/env python3
"""
CLI for Portfolio Review - Display comprehensive LP and balance information.
"""

import argparse
import sys
import json

from .config import Settings
from .portfolio_review import display_portfolio_review, get_portfolio_review
from .logging_config import setup_logging, get_logger

logger = get_logger(__name__)


def cli_main():
    """CLI entry point for portfolio review."""
    parser = argparse.ArgumentParser(
        description="Portfolio Review - Display LP positions and balances",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output in JSON format'
    )
    
    parser.add_argument(
        '--no-telegram',
        action='store_true',
        help='Skip sending to Telegram'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    
    try:
        settings = Settings.from_env()
        
        if args.no_telegram:
            # Just get review without notifications
            review = get_portfolio_review(settings)
            if not args.json:
                print(review.format_display())
        else:
            # Display and send to Telegram
            review = display_portfolio_review(settings)
        
        if args.json:
            print(json.dumps(review.to_dict(), indent=2))
            
    except Exception as e:
        logger.error(f"❌ Failed to generate portfolio review: {e}")
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    cli_main()