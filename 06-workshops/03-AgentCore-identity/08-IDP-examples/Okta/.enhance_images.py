#!/usr/bin/env python3
"""
Script to add red highlights and arrows to radio buttons in Okta setup images
"""

from PIL import Image, ImageDraw
import os


def add_radio_button_highlights(image_path, output_path, radio_positions):
    """
    Add red highlights to radio button positions
    """
    # Open the image
    img = Image.open(image_path)
    draw = ImageDraw.Draw(img)

    # Red color for highlights
    red_color = (255, 0, 0)

    for x, y in radio_positions:
        # Draw thick red circle around radio button
        radius = 20
        for r in range(4):  # Multiple circles for thickness
            draw.ellipse(
                [x - radius - r, y - radius - r, x + radius + r, y + radius + r],
                outline=red_color,
                width=3,
            )

    # Save the enhanced image
    img.save(output_path)
    print(f"Enhanced image saved: {output_path}")


def add_box_highlights(image_path, output_path, box_positions):
    """
    Add red box highlights to specific areas
    """
    # Open the image
    img = Image.open(image_path)
    draw = ImageDraw.Draw(img)

    # Red color for highlights
    red_color = (255, 0, 0)

    for x1, y1, x2, y2 in box_positions:
        # Draw thick red rectangle
        for r in range(4):
            draw.rectangle([x1 - r, y1 - r, x2 + r, y2 + r], outline=red_color, width=3)

    # Save the enhanced image
    img.save(output_path)
    print(f"Enhanced image saved: {output_path}")


def main():
    base_dir = "/Users/suramac/amazon-bedrock-agentcore-samples/03-integrations/IDP-examples/Okta/images"

    # Image 2.png - Radio buttons for sign-in method and application type
    image2_positions = [
        (388, 65),  # OIDC - OpenID Connect radio button
        (388, 473),  # Web Application radio button
    ]

    add_radio_button_highlights(
        os.path.join(base_dir, "2.png"),
        os.path.join(base_dir, "2_enhanced.png"),
        image2_positions,
    )

    # Image 5.png - Radio buttons for controlled access and enable immediate access
    image5_positions = [  # noqa: F841
        (362, 62),  # Allow everyone in your organization to access radio button
        (362, 194),  # Enable immediate access checkbox (treated as radio button)
    ]

    # Image 3.png - Radio buttons and checkboxes for grant type
    image3_positions = [
        (408, 364),  # Authorization Code checkbox (selected)
    ]

    add_radio_button_highlights(
        os.path.join(base_dir, "3.png"),
        os.path.join(base_dir, "3_enhanced.png"),
        image3_positions,
    )

    # Image 6.png - Radio buttons for client authentication and highlight client credentials
    image6_positions = [
        (317, 391),  # Client secret radio button (selected)
        (653, 289),  # Client ID copy button
        (530, 725),  # Client secret copy button
    ]

    add_radio_button_highlights(
        os.path.join(base_dir, "6.png"),
        os.path.join(base_dir, "6_enhanced.png"),
        image6_positions,
    )

    # Image 7.png - Box highlight for Issuer Metadata URI
    image7_boxes = [
        (20, 462, 300, 511),  # Issuer Metadata URI label box (moved down)
    ]

    add_box_highlights(
        os.path.join(base_dir, "7.png"),
        os.path.join(base_dir, "7_enhanced.png"),
        image7_boxes,
    )


if __name__ == "__main__":
    main()
