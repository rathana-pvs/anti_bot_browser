"""Test Vision Engine and Template Matching on profile_003."""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from engine.container_client import ContainerClient
from engine.vision import VisionEngine

def main():
    profile_id = "profile_003"
    client = ContainerClient(profile_id)
    vision = VisionEngine(client)

    print(f"Testing VisionEngine on {profile_id}...")

    # Test 1: Locate Facebook logo
    print("Locating 'facebook_logo'...")
    start = time.time()
    logo_pos = vision.find_element("facebook_logo", threshold=0.85)
    elapsed = (time.time() - start) * 1000
    print(f"Logo position: {logo_pos} (found in {elapsed:.1f}ms)")
    assert logo_pos is not None, "Failed to locate facebook_logo template!"

    # Test 2: Locate Log In button
    print("Locating 'facebook_login_btn'...")
    start = time.time()
    login_btn_pos = vision.find_element("facebook_login_btn", threshold=0.75)
    elapsed = (time.time() - start) * 1000
    print(f"Log In button position: {login_btn_pos} (found in {elapsed:.1f}ms)")
    assert login_btn_pos is not None, "Failed to locate facebook_login_btn template!"

    # Test 3: Check that coordinates were cached
    print("Checking element cache...")
    assert "facebook_logo" in vision._cache, "Logo not cached!"
    assert "facebook_login_btn" in vision._cache, "Login button not cached!"
    print(f"Cache content: {vision._cache}")

    print("\n✅ Vision Engine & Template Matching: ALL TESTS PASSED!")

if __name__ == "__main__":
    main()
