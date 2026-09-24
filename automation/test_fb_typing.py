"""Test clicking and typing into the email input on Facebook in profile_003."""

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

    # Focus window
    client.ensure_focus()

    # Move and click email input
    email_x, email_y = 1580, 365
    print(f"Moving to and clicking email box at ({email_x}, {email_y})...")
    human.click(email_x, email_y)
    time.sleep(0.3)

    # Type sample email
    sample_text = "test_user_automation@fb.test"
    print(f"Typing '{sample_text}' at natural human cadence...")
    human.type_text(sample_text, wpm=65)
    time.sleep(0.5)

    # Capture screenshot to verify
    out_path = "/home/rathana/.gemini/antigravity/brain/bf3cf9fe-1df8-4f3e-8a86-38ffa0e73e42/test_fb_typed.png"
    client.save_screenshot(out_path)
    print(f"Saved test screenshot to: {out_path}")

if __name__ == "__main__":
    main()
