"""Test human typing and keyboard interactions on profile_003."""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from engine.container_client import ContainerClient
from engine.human_input import HumanInput

def main():
    profile_id = "profile_003"
    client = ContainerClient(profile_id)
    human = HumanInput(client)

    w, h = client.get_screen_dimensions()
    
    # Click near the search input box (approx x=960, y=455)
    search_x = w // 2
    search_y = int(h * 0.42)
    print(f"Clicking search input box at ({search_x}, {search_y})...")
    human.click(search_x, search_y)
    time.sleep(0.5)

    # Type query at human speed
    query = "facebook.com login"
    print(f"Typing '{query}' at natural human cadence...")
    human.type_text(query, wpm=60)
    time.sleep(0.5)

    # Press Enter
    print("Pressing Return key...")
    human.key_press("Return")
    time.sleep(3.0)

    # Capture result screenshot
    out_path = "/home/rathana/.gemini/antigravity/brain/bf3cf9fe-1df8-4f3e-8a86-38ffa0e73e42/test_search_results.png"
    client.save_screenshot(out_path)
    print(f"Saved search results screenshot to: {out_path}")

if __name__ == "__main__":
    main()
