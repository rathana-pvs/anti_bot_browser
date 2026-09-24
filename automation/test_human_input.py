"""Test Human Input and Container Client on running profile_003."""

import os
import sys
import time

# Add automation directory to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from engine.container_client import ContainerClient
from engine.human_input import HumanInput

def main():
    profile_id = "profile_003"
    print(f"Connecting to container client for {profile_id}...")
    client = ContainerClient(profile_id)

    if not client.is_running():
        print(f"Error: {client.container_name} is not running!")
        sys.exit(1)

    print(f"Container {client.container_name} is active.")

    w, h = client.get_screen_dimensions()
    print(f"Screen resolution: {w}x{h}")

    init_x, init_y = client.get_mouse_position()
    print(f"Initial mouse position: ({init_x}, {init_y})")

    human = HumanInput(client)

    # Test 1: Smooth Bezier movement to center of screen
    target_x = w // 2
    target_y = h // 2
    print(f"Moving smoothly to center: ({target_x}, {target_y})...")
    start_time = time.time()
    human.move_to(target_x, target_y)
    elapsed = time.time() - start_time
    final_x, final_y = client.get_mouse_position()
    print(f"Arrived at: ({final_x}, {final_y}) in {elapsed:.2f}s")
    assert abs(final_x - target_x) <= 3 and abs(final_y - target_y) <= 3, "Mouse did not arrive at target!"

    # Test 2: Natural click
    print("Testing human click...")
    human.click()

    # Test 3: Capture screenshot
    print("Capturing virtual display screenshot...")
    img = client.screenshot()
    print(f"Captured screenshot shape: {img.shape} (H x W x C)")
    out_path = "/home/rathana/.gemini/antigravity/brain/bf3cf9fe-1df8-4f3e-8a86-38ffa0e73e42/test_human_shot.png"
    client.save_screenshot(out_path)
    print(f"Saved test screenshot to: {out_path}")

    print("\n✅ Human Input & Container Client Verification: PASSED!")

if __name__ == "__main__":
    main()
