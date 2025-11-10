#!/usr/bin/env python3
"""
Test script for the GitHub version checker functionality
"""

import sys
import os
import json

# Add the plugin directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_version_checker():
    """Test the version checker functionality"""
    print("Testing GitHub Version Checker for Dispatcharr Plugin...")
    print("-" * 60)

    # Import the plugin module components
    try:
        from plugin import Plugin
        print("✓ Successfully imported Plugin class")
    except Exception as e:
        print(f"✗ Failed to import Plugin: {e}")
        return False

    # Create plugin instance
    try:
        plugin = Plugin()
        print(f"✓ Successfully created plugin instance")
        print(f"  Plugin name: {plugin.name}")
        print(f"  Plugin version: {plugin.version}")
    except Exception as e:
        print(f"✗ Failed to create plugin instance: {e}")
        return False

    # Test fields property
    try:
        fields = plugin.fields
        print(f"✓ Successfully retrieved fields property")
        print(f"  Total fields: {len(fields)}")
    except Exception as e:
        print(f"✗ Failed to get fields property: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Check if version status field exists
    version_field = None
    for field in fields:
        if field.get('id') == 'version_status':
            version_field = field
            break

    if version_field:
        print(f"✓ Found version_status field")
        print(f"  Label: {version_field.get('label')}")
        print(f"  Type: {version_field.get('type')}")
        print(f"  Message: {version_field.get('help_text')}")
    else:
        print("✗ version_status field not found in fields list")
        return False

    # Test version check methods directly
    print("\n" + "-" * 60)
    print("Testing version check methods directly...")

    try:
        # Test _get_latest_version
        print("\nTesting _get_latest_version()...")
        latest = plugin._get_latest_version("PiratesIRC", "Dispatcharr-Event-Channel-Managarr-Plugin")
        print(f"  Latest version from GitHub: {latest}")

        # Test _should_check_for_updates
        print("\nTesting _should_check_for_updates()...")
        should_check = plugin._should_check_for_updates()
        print(f"  Should check for updates: {should_check}")

        if plugin.cached_version_info:
            print(f"  Cached version info: {plugin.cached_version_info}")

    except Exception as e:
        print(f"✗ Error testing version check methods: {e}")
        import traceback
        traceback.print_exc()
        return False

    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    return True

if __name__ == "__main__":
    success = test_version_checker()
    sys.exit(0 if success else 1)
